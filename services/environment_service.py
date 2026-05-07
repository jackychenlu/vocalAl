import os
import shutil
import ssl
import subprocess
import threading
import urllib.request
import zipfile
from pathlib import Path
from tkinter import messagebox
import tkinter as tk
from typing import Callable


class EnvironmentService:
    def __init__(
        self,
        app_dir: Path,
        bin_dir: Path,
        py_dir: Path,
        lib_dir: Path,
        gpu_lib_dir: Path,
        models_dir: Path,
        local_python: Path,
        subp_flags: int,
        log_fn: Callable,
        progress_fn: Callable,
        status_fn: Callable,
        root: tk.Tk,
        get_device_fn: Callable[[], str],
        set_device_fn: Callable[[str], None],
        get_is_processing_fn: Callable[[], bool],
        start_setup_fn: Callable[[str], None],
    ):
        self.app_dir = app_dir
        self.bin_dir = bin_dir
        self.py_dir = py_dir
        self.lib_dir = lib_dir
        self.gpu_lib_dir = gpu_lib_dir
        self.models_dir = models_dir
        self.local_python = local_python
        self.subp_flags = subp_flags
        self.log = log_fn
        self._progress = progress_fn
        self._status = status_fn
        self._root = root
        self._get_device = get_device_fn
        self._set_device = set_device_fn
        self._get_is_processing = get_is_processing_fn
        self._start_setup = start_setup_fn

        self._startup_ort_check_running = False
        self._startup_component_prompt_shown = False
        self._ort_fix_prompt_after_id = None
        self._ort_fix_prompt_pending = False
        self._ort_fix_prompt_active = False
        self._ort_fix_prompt_shown_keys: set[str] = set()
        self._ort_fix_prompt_suppressed_keys: set[str] = set()
        self._last_log_percent = -1

    # ------------------------------------------------------------------
    # Python environment helpers (used by SeparationService)
    # ------------------------------------------------------------------

    def build_python_env(self, lib_dir: Path, include_gpu_runtime: bool = False) -> dict:
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env["PYTHONPATH"] = str(lib_dir)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        search_paths = [str(self.py_dir), str(self.bin_dir), str(lib_dir)]
        ort_pkg_dir = lib_dir / "onnxruntime"
        ort_capi_dir = ort_pkg_dir / "capi"
        if ort_pkg_dir.exists():
            search_paths.append(str(ort_pkg_dir))
        if ort_capi_dir.exists():
            search_paths.append(str(ort_capi_dir))
        if include_gpu_runtime and lib_dir.exists():
            for p in lib_dir.rglob("bin"):
                search_paths.append(str(p))
            for p in lib_dir.rglob("lib"):
                search_paths.append(str(p))

        deduped: list[str] = []
        seen: set[str] = set()
        for p in search_paths:
            if p and p not in seen:
                deduped.append(p)
                seen.add(p)

        env["PATH"] = os.pathsep.join(deduped) + os.pathsep + env.get("PATH", "")
        return env

    def _probe_onnxruntime_stack(self, lib_dir: Path, expect_gpu: bool = False) -> str:
        if not self.local_python.exists() or not lib_dir.exists():
            return "STACK_MISSING"

        env = self.build_python_env(lib_dir, include_gpu_runtime=expect_gpu)
        lib_dir_posix = str(lib_dir).replace("\\", "/")
        check_script = f"""
import sys, os
target_lib = r'{lib_dir_posix}'
if target_lib not in sys.path:
    sys.path.insert(0, target_lib)

if hasattr(os, 'add_dll_directory'):
    dll_dirs = [target_lib]
    ort_pkg = os.path.join(target_lib, 'onnxruntime')
    ort_capi = os.path.join(ort_pkg, 'capi')
    for p in [ort_pkg, ort_capi]:
        if os.path.isdir(p):
            dll_dirs.append(p)
    if {str(expect_gpu)}:
        for root, dirs, files in os.walk(target_lib):
            for sub in ['bin', 'lib']:
                p = os.path.join(root, sub)
                if os.path.isdir(p):
                    dll_dirs.append(p)
    for p in dll_dirs:
        try:
            os.add_dll_directory(p)
        except Exception:
            pass
try:
    import onnxruntime as ort
    providers = ort.get_available_providers()
    if {str(expect_gpu)}:
        if 'CUDAExecutionProvider' in providers:
            print('ORT_OK_GPU')
        else:
            print(f'ORT_NO_CUDA {{providers}}')
    else:
        print('ORT_OK_CPU')
except Exception as e:
    err = str(e)
    err_lower = err.lower()
    if 'dll' in err_lower or '初始化' in err or 'initialization routine' in err_lower:
        if any(token in err_lower for token in ['vcruntime', 'msvcp', 'api-ms-win-crt', 'ucrtbase']):
            print(f'ORT_DLL_FAIL:VC_RUNTIME_MISSING:{{err[:220]}}')
        else:
            print(f'ORT_DLL_FAIL:{{err[:220]}}')
    else:
        if 'No module named' in err:
            print(f'ORT_ERR:{{err}} (Search Path: {{target_lib}})')
        else:
            print(f'ORT_ERR:{{err[:120]}}')
"""
        try:
            res = subprocess.run(
                [str(self.local_python), "-c", check_script],
                capture_output=True, text=True, env=env,
                creationflags=self.subp_flags, timeout=30,
                encoding="utf-8", errors="replace",
            )
            return (res.stdout or "").strip() or "ORT_NO_OUTPUT"
        except subprocess.TimeoutExpired:
            return "ORT_TIMEOUT"
        except Exception as e:
            return f"ORT_ERR:{str(e)}"

    def ensure_runtime_stack_ready(self, device: str) -> tuple[bool, str, Path]:
        is_gpu = (device == "cuda")
        runtime_lib_dir = self.gpu_lib_dir if is_gpu else self.lib_dir
        expected = "ORT_OK_GPU" if is_gpu else "ORT_OK_CPU"
        diag_out = self._probe_onnxruntime_stack(runtime_lib_dir, expect_gpu=is_gpu)
        self.log(f"🔍 運算環境診斷: {diag_out}")

        if diag_out == expected:
            return True, device, runtime_lib_dir

        if is_gpu:
            self.log("⚠️ GPU 核心尚未就緒，已切換至獨立 CPU 核心繼續執行。")
            self._root.after(0, lambda: self._set_device("cpu"))
            self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
            return self.ensure_runtime_stack_ready("cpu")

        repairable_tokens = ["ORT_ERR", "STACK_MISSING", "ORT_DLL_FAIL", "ORT_NO_OUTPUT", "ORT_TIMEOUT"]
        if any(token in diag_out for token in repairable_tokens):
            self.log("🛠️ 偵測到 CPU AI 核心缺失或損壞，正在自動補齊必要組件...")
            self._status("正在修復 CPU AI 核心...", "orange")

            if self.install_packages_locally(install_mode="cpu"):
                retry_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
                self.log(f"🔁 CPU 修復後再次診斷: {retry_out}")
                if retry_out == "ORT_OK_CPU":
                    self.log("✅ CPU AI 核心已自動修復完成，繼續執行音訊分離。")
                    return True, "cpu", self.lib_dir
                self.log(f"❌ CPU 核心修復後仍無法正常載入：{retry_out}")
            else:
                self.log("❌ 自動修復 CPU AI 核心失敗。")

        self.log("❌ CPU 核心無法正常載入，請執行「一鍵修復/初始化環境」。")
        return False, "cpu", self.lib_dir

    def _get_target_ai_dir(self, install_mode: str = "auto") -> Path:
        if install_mode == "cpu":
            return self.lib_dir
        if install_mode == "gpu":
            return self.gpu_lib_dir
        return self.gpu_lib_dir if self._is_nvidia_gpu_present() else self.lib_dir

    def _has_onnxruntime_package(self, target_dir: Path) -> bool:
        try:
            pkg_dir = target_dir / "onnxruntime"
            return pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # pth fix
    # ------------------------------------------------------------------

    def fix_python_pth(self):
        try:
            pth_files = list(self.py_dir.glob("*._pth"))
            if not pth_files:
                self.log("⚠️ 找不到 Python .pth 設定檔，跳過路徑校正。")
                return
            pth_file = pth_files[0]
            with open(pth_file, "r") as f:
                lines = f.readlines()

            lines = [l.strip() for l in lines if l.strip()]
            removed_legacy = False
            legacy_entries = {"..\\ai_libraries", "..\\ai_libraries_gpu"}
            filtered_lines = []
            for line in lines:
                if line in legacy_entries:
                    removed_legacy = True
                    continue
                filtered_lines.append(line)
            lines = filtered_lines

            py_zip = next((f.name for f in self.py_dir.glob("python*.zip")), "python310.zip")
            required = [py_zip, ".", "Lib/site-packages", "import site"]
            needs_update = removed_legacy

            for item in required:
                if item not in lines:
                    if f"#{item}" in lines:
                        lines[lines.index(f"#{item}")] = item
                    else:
                        lines.append(item)
                    needs_update = True

            if needs_update:
                with open(pth_file, "w") as f:
                    f.write("\n".join(lines) + "\n")
                self.log("🔧 已校正 Python 路徑設定檔（改為執行時動態注入 CPU/GPU 套件路徑）。")
        except Exception as e:
            self.log(f"⚠️ 路徑校正失敗: {str(e)}")

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    def _download_reporthook(self, count, block_size, total_size):
        if total_size > 0:
            percent = min(int(count * block_size * 100 / total_size), 100)
            self._progress(percent, "正在下載")
            if percent % 10 == 0 and percent != self._last_log_percent:
                self.log(f"  > 下載進度: {percent}%")
                self._last_log_percent = percent

    def download_portable_python(self) -> bool:
        py_urls = [
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip",
        ]

        try:
            self.py_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"❌ 無法建立 Python 目錄: {self.py_dir}")
            self.log(f"   原因: {str(e)}")
            self.log("💡 請手動建立該資料夾，或將程式移至桌面等較短路徑後重試。")
            return False

        if not self.py_dir.exists():
            self.log(f"❌ 目錄建立後仍不存在（可能是權限問題）: {self.py_dir}")
            return False

        _test_file = self.py_dir / ".write_test"
        try:
            _test_file.write_text("ok")
            _test_file.unlink()
        except Exception as e:
            self.log(f"❌ 目錄無寫入權限: {self.py_dir}")
            self.log(f"   原因: {str(e)}")
            self.log("💡 請以系統管理員身份執行程式，或更換輸出目錄位置。")
            return False

        zip_path = self.py_dir / "py.zip"
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass

        ssl_context = ssl._create_unverified_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
        opener.addheaders = [("User-agent", "Mozilla/5.0")]
        urllib.request.install_opener(opener)

        for attempt, url in enumerate(py_urls, 1):
            self.log(f"🚀 正在下載 Python 核心 (來源 {attempt}/{len(py_urls)})...")
            try:
                self._last_log_percent = -1
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)

                if not zip_path.exists() or zip_path.stat().st_size < 1024:
                    self.log(f"⚠️ 來源 {attempt} 下載的檔案過小或不存在，嘗試下一個...")
                    zip_path.unlink(missing_ok=True)
                    continue

                if not zipfile.is_zipfile(str(zip_path)):
                    self.log(f"⚠️ 來源 {attempt} 下載的檔案損壞，嘗試下一個...")
                    zip_path.unlink(missing_ok=True)
                    continue

                self.log("📦 正在解壓縮 Python...")
                with zipfile.ZipFile(str(zip_path), "r") as zip_ref:
                    zip_ref.extractall(str(self.py_dir))

                self.fix_python_pth()
                zip_path.unlink(missing_ok=True)
                self.log(f"✅ Python 核心安裝完成（來源 {attempt}）。")
                return True
            except Exception as e:
                err_msg = str(e)
                self.log(f"⚠️ 來源 {attempt} 下載失敗: {err_msg}")
                if "No such file or directory" in err_msg:
                    self.log(f"   ⚠️ 寫入路徑失敗，目標目錄可能在下載過程中消失或被鎖定。")
                    self.log(f"   目標路徑: {zip_path}")
                zip_path.unlink(missing_ok=True)
                if attempt < len(py_urls):
                    self.log("🔄 嘗試下一個備用來源...")

        self.log("❌ Python 核心所有下載來源均失敗。")
        self.log("💡 可能原因：(1) 網路連線問題  (2) 防火牆封鎖  (3) 磁碟空間不足")
        self.log("💡 請確認網路正常後重試，或手動下載 Python embed zip 放入 runtime_python 資料夾。")
        return False

    def download_ffmpeg(self) -> bool:
        primary_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
        fallback_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = self.bin_dir / "ffmpeg.zip"

        try:
            self.bin_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"❌ 無法建立 FFmpeg 目錄: {self.bin_dir}\n   原因: {str(e)}")
            return False
        _test = self.bin_dir / ".write_test"
        try:
            _test.write_text("ok")
            _test.unlink()
        except Exception as e:
            self.log(f"❌ FFmpeg 目錄無寫入權限: {self.bin_dir}\n   原因: {str(e)}")
            return False

        for attempt, url in enumerate([primary_url, fallback_url], 1):
            try:
                self.log(f"🚀 正在連線至下載伺服器 (來源 {attempt}/2)...")
                ssl_context = ssl._create_unverified_context()
                self._last_log_percent = -1
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
                opener.addheaders = [("User-agent", "Mozilla/5.0")]
                urllib.request.install_opener(opener)
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)
                self.log("📦 正在提取 FFmpeg 引擎與共享函式庫 (DLLs)...")
                with zipfile.ZipFile(str(zip_path), "r") as zip_ref:
                    for file in zip_ref.namelist():
                        normalized_file = file.replace("\\", "/")
                        if "/bin/" in normalized_file and (normalized_file.endswith(".exe") or normalized_file.endswith(".dll")):
                            filename = os.path.basename(normalized_file)
                            with zip_ref.open(file) as source, open(self.bin_dir / filename, "wb") as target:
                                shutil.copyfileobj(source, target)
                if zip_path.exists():
                    os.remove(str(zip_path))
                return True
            except Exception as e:
                self.log(f"⚠️ 來源 {attempt} 下載失敗: {str(e)}")
                if zip_path.exists():
                    try:
                        os.remove(str(zip_path))
                    except Exception:
                        pass
                if attempt < 2:
                    self.log("🔄 嘗試備用下載來源...")

        self.log("❌ FFmpeg 所有下載來源均失敗，請檢查網路連線。")
        return False

    # ------------------------------------------------------------------
    # AI package installation
    # ------------------------------------------------------------------

    def _clean_ai_packages_in_dir(self, target_dir: Path):
        patterns = [
            "torch*", "torchvision*", "torchaudio*",
            "onnxruntime*", "onnxruntime_gpu*",
            "audio_separator*",
            "nvidia*",
        ]
        removed_any = False
        for pattern in patterns:
            for p in target_dir.glob(pattern):
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink()
                    removed_any = True
                except Exception as e:
                    self.log(f"  ⚠️ 清理舊組件失敗: {p.name} ({str(e)})")
        if removed_any:
            self.log(f"  ✅ 已清理舊組件: {target_dir.name}")

    def _install_ai_stack(self, target_dir: Path, target_mode: str = "cpu", is_rtx50: bool = False) -> bool:
        target_dir.mkdir(parents=True, exist_ok=True)

        if target_mode == "cpu":
            self.log("📦 正在部署獨立 CPU AI 核心...")
            torch_index = "https://download.pytorch.org/whl/cpu"
            install_steps = [
                ["setuptools", "wheel", "pip"],
                ["--extra-index-url", torch_index,
                 "torch==2.5.1+cpu", "torchvision==0.20.1+cpu", "torchaudio==2.5.1+cpu",
                 "onnxruntime", "audio-separator"],
            ]
        else:
            if is_rtx50:
                self.log("📦 正在部署獨立 GPU AI 核心（cu128 / RTX 50）...")
                torch_index = "https://download.pytorch.org/whl/cu128"
                torch_ver, tv_ver, ta_ver = "2.7.1+cu128", "0.22.1+cu128", "2.7.1+cu128"
            else:
                self.log("📦 正在部署獨立 GPU AI 核心（cu124）...")
                torch_index = "https://download.pytorch.org/whl/cu124"
                torch_ver, tv_ver, ta_ver = "2.5.1+cu124", "0.20.1+cu124", "2.5.1+cu124"
            install_steps = [
                ["setuptools", "wheel", "pip"],
                ["nvidia-cuda-runtime-cu12", "nvidia-cudnn-cu12", "nvidia-cublas-cu12",
                 "nvidia-curand-cu12", "nvidia-cufft-cu12", "nvidia-cuda-nvrtc-cu12", "nvidia-ml-py"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime-gpu", "audio-separator[gpu]"],
            ]

        self._clean_ai_packages_in_dir(target_dir)

        pip_base_cmd = [
            str(self.local_python), "-m", "pip", "install",
            "--target", str(target_dir),
            "--upgrade",
            "--retries", "10",
            "--timeout", "100",
            "--no-warn-script-location",
        ]
        pip_env = self.build_python_env(target_dir, include_gpu_runtime=(target_mode == "gpu"))

        for i, step_pkgs in enumerate(install_steps):
            self.log(f"📦 正在執行安裝進度 ({i+1}/{len(install_steps)}): {' '.join(step_pkgs[-3:])}...")
            cmd = pip_base_cmd + step_pkgs
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, creationflags=self.subp_flags, encoding="utf-8",
                errors="replace", env=pip_env,
            )
            has_output = False
            installing_phase = False
            heartbeat_stop = threading.Event()

            def _heartbeat(stop_evt, step=i):
                tick = 0
                while not stop_evt.wait(20):
                    tick += 20
                    self.log(f"  ⏳ 套件寫入中，請稍候... (已等待 {tick} 秒)")

            heartbeat_thread = None
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    has_output = True
                    clean_line = line.strip()
                    if any(x in clean_line for x in ["Downloading", "Installing", "Collecting", "ERROR", "Exception", "Traceback", "Requirement already satisfied"]):
                        if "satisfied" in clean_line and len(clean_line) > 100:
                            clean_line = clean_line[:100] + "..."
                        self.log(f"  > {clean_line}")
                        if "Downloading" in clean_line:
                            self._status(f"正在下載組件 ({i+1}/{len(install_steps)})...", "orange")
                        if "Installing collected packages" in clean_line and not installing_phase:
                            installing_phase = True
                            self._status(f"正在寫入套件 ({i+1}/{len(install_steps)})，請勿關閉...", "orange")
                            heartbeat_thread = threading.Thread(target=_heartbeat, args=(heartbeat_stop,), daemon=True)
                            heartbeat_thread.start()

            heartbeat_stop.set()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=1)
            process.wait()
            if process.returncode != 0:
                self.log(f"❌ 第 {i+1} 階段安裝失敗 (代碼: {process.returncode})。")
                return False
            if not has_output and i > 0:
                self.log(f"⚠️ 第 {i+1} 階段安裝似乎沒有輸出，請檢查環境。")

        if not (target_dir / "torch").exists():
            self.log(f"❌ 安裝程序已結束，但未能在 {target_dir.name} 中找到 torch。")
            return False
        if not (target_dir / "audio_separator").exists():
            self.log(f"❌ 安裝程序已結束，但未能在 {target_dir.name} 中找到 audio_separator。")
            return False
        if not self._has_onnxruntime_package(target_dir):
            self.log(f"❌ 安裝程序已結束，但未能在 {target_dir.name} 中找到 onnxruntime。")
            return False
        return True

    def install_packages_locally(self, install_mode: str = "auto") -> bool:
        try:
            self.log("📥 下載 Pip 安裝工具...")
            pip_script = self.py_dir / "get-pip.py"
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", pip_script)

            self.log("📥 正在安裝 Pip 組件...")
            subprocess.run([str(self.local_python), str(pip_script)], creationflags=self.subp_flags, check=True)

            pip_check = subprocess.run(
                [str(self.local_python), "-m", "pip", "--version"],
                capture_output=True, text=True, creationflags=self.subp_flags,
                encoding="utf-8", errors="replace",
            )
            if pip_check.returncode != 0:
                self.log("❌ Pip 安裝失敗，無法繼續。")
                return False
            self.log(f"✅ Pip 已就緒: {pip_check.stdout.strip()}")
            self.log(f"📥 正在準備 AI 運算環境 (模式: {install_mode})...")

            has_nvidia_gpu = self._is_nvidia_gpu_present()
            is_rtx50 = False
            if has_nvidia_gpu:
                try:
                    res = subprocess.run(
                        ["nvidia-smi", "-L"],
                        capture_output=True, text=True,
                        creationflags=self.subp_flags, timeout=10,
                        encoding="utf-8", errors="replace",
                    )
                    if res.returncode == 0 and "RTX 50" in res.stdout:
                        is_rtx50 = True
                except Exception:
                    pass

            if any(ord(c) > 127 for c in str(self.app_dir)):
                self.log("⚠️ 偵測到路徑中含有中文或特殊字元，這極易導致安裝失敗。")
                self.log("💡 強烈建議：將程式資料夾移至磁碟根目錄 (例如 C:\\mp3_tool)，避免路徑問題。")

            install_targets = []
            if install_mode == "cpu":
                self.log("ℹ️ 使用者選擇強制安裝 CPU 版本。")
                install_targets = [(self.lib_dir, "cpu", False)]
            elif install_mode == "gpu":
                if not has_nvidia_gpu:
                    self.log("❌ 目前未偵測到 NVIDIA 顯示卡，無法安裝純 GPU 版本。")
                    return False
                self.log("ℹ️ 使用者選擇強制安裝 GPU 版本。")
                install_targets = [(self.gpu_lib_dir, "gpu", is_rtx50)]
            elif install_mode == "both":
                self.log("ℹ️ 使用者選擇安裝 CPU + GPU 雙支援版（分開存放）。")
                install_targets = [(self.lib_dir, "cpu", False)]
                if has_nvidia_gpu:
                    install_targets.append((self.gpu_lib_dir, "gpu", is_rtx50))
                else:
                    self.log("⚠️ 目前未偵測到 NVIDIA 顯示卡，本次僅安裝 CPU 套件。")
            else:
                if not has_nvidia_gpu:
                    self.log("ℹ️ 未偵測到 NVIDIA 顯示卡，將安裝 CPU 版本。")
                    install_targets = [(self.lib_dir, "cpu", False)]
                elif is_rtx50:
                    self.log("🚀 偵測到 RTX 50 系列，將安裝獨立 GPU cu128 核心。")
                    install_targets = [(self.gpu_lib_dir, "gpu", True)]
                else:
                    self.log("✅ 偵測到 NVIDIA 顯示卡，將安裝獨立 GPU cu124 核心。")
                    install_targets = [(self.gpu_lib_dir, "gpu", False)]

            for target_dir, target_mode, target_is_rtx50 in install_targets:
                if not self._install_ai_stack(target_dir, target_mode=target_mode, is_rtx50=target_is_rtx50):
                    return False

            if pip_script.exists():
                os.remove(pip_script)
            return True
        except Exception as e:
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                self.log("❌ 磁碟空間不足，無法繼續安裝 AI 組件。")
                self.log("💡 建議先釋放磁碟空間後再重試。")
                self.log("💡 若不需要 GPU 加速，請改選「僅安裝 CPU 版」，所需空間會比雙支援版少很多。")
            else:
                self.log(f"安裝錯誤: {str(e)}")
            return False

    # ------------------------------------------------------------------
    # GPU / ORT detection
    # ------------------------------------------------------------------

    def _is_nvidia_gpu_present(self) -> bool:
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "Name"],
                capture_output=True, text=True, timeout=10,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                return "NVIDIA" in result.stdout.upper()
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Display | Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=15,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace",
            )
            if "NVIDIA" in result.stdout.upper():
                return True
        except Exception:
            pass
        return False

    def _quick_check_gpu(self) -> bool:
        if not self.local_python.exists():
            return False
        if not self._is_nvidia_gpu_present():
            return False
        if not (self.gpu_lib_dir / "torch").exists():
            return False
        return self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True) == "ORT_OK_GPU"

    def _startup_ort_check(self):
        if not self.local_python.exists():
            return
        if self._startup_ort_check_running:
            self.log("ℹ️ 啟動環境檢測已在執行中，略過重複請求。")
            return

        self._startup_ort_check_running = True
        try:
            cpu_out = self._probe_onnxruntime_stack(self.lib_dir, expect_gpu=False)
            gpu_out = "STACK_SKIPPED"
            has_nvidia_gpu = self._is_nvidia_gpu_present()

            if has_nvidia_gpu and (self.gpu_lib_dir / "torch").exists():
                gpu_out = self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True)

            if gpu_out == "ORT_OK_GPU":
                self.log("✅ 偵測到獨立 GPU 核心已就緒，自動切換至 GPU 模式。")
                self._root.after(0, lambda: self._set_device("gpu"))
                self._reset_ort_fix_prompt_state(clear_history=False)
            elif cpu_out == "ORT_OK_CPU":
                self.log("✅ 基礎環境已就緒（CPU 模式）。")
                if self._get_device() == "gpu":
                    self._root.after(0, lambda: self._set_device("cpu"))
                self._reset_ort_fix_prompt_state(clear_history=False)
            else:
                self.log(f"ℹ️ CPU 核心檢測結果: {cpu_out}")

            if has_nvidia_gpu:
                if gpu_out not in ["STACK_SKIPPED", "STACK_MISSING"]:
                    self.log(f"🔍 GPU 核心檢測結果: {gpu_out}")
                if gpu_out in ["ORT_DLL_FAIL", "ORT_NO_OUTPUT"] and cpu_out == "ORT_OK_CPU":
                    self.log("⚠️ GPU 核心存在但無法載入，已保留 CPU 模式，不影響純 CPU 使用。")
            elif cpu_out == "ORT_OK_CPU":
                self.log("💡 未偵測到 NVIDIA 顯示卡，CPU 模式為正常運行狀態。")
        except Exception as e:
            self.log(f"ℹ️ 啟動時環境檢測失敗: {str(e)}")
        finally:
            self._startup_ort_check_running = False

    # ------------------------------------------------------------------
    # yt-dlp management
    # ------------------------------------------------------------------

    def _is_ytdlp_installed(self) -> bool:
        if (self.py_dir / "Scripts" / "yt-dlp.exe").exists():
            return True
        if (self.lib_dir / "yt_dlp" / "__main__.py").exists():
            return True
        return False

    def _check_ytdlp(self):
        if not self._is_ytdlp_installed():
            self.log("🚀 偵測到缺少 YouTube 下載組件，正在自動補齊...")
            threading.Thread(target=self._install_ytdlp_silent, daemon=True).start()

    def _install_ytdlp_silent(self):
        try:
            result = subprocess.run(
                [str(self.local_python), "-m", "pip", "install", "--upgrade", "yt-dlp",
                 "--target", str(self.lib_dir), "--no-warn-script-location"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, creationflags=self.subp_flags,
            )
            if result.returncode == 0:
                self.log("✅ YouTube 下載組件已補齊。")
            else:
                self.log(f"❌ YouTube 下載組件安裝失敗: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            self.log("❌ YouTube 下載組件安裝逾時（超過 120 秒），請手動點擊「初始化/修復環境」。")
        except Exception as e:
            self.log(f"❌ YouTube 下載組件安裝出錯: {str(e)}")

    # ------------------------------------------------------------------
    # ORT fix prompt management
    # ------------------------------------------------------------------

    def _schedule_ort_fix_prompt(self, issue_key: str = "gpu_runtime_fallback", delay_ms: int = 0):
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            return
        if self._ort_fix_prompt_active or self._ort_fix_prompt_pending:
            return
        if issue_key in self._ort_fix_prompt_shown_keys:
            return

        self._ort_fix_prompt_pending = True
        self.log(f"ℹ️ [PROMPT] 已排程修復提示: {issue_key} ({delay_ms}ms)")

        def _fire():
            self._ort_fix_prompt_after_id = None
            self._ort_fix_prompt_pending = False
            self._prompt_ort_fix(issue_key=issue_key)

        self._ort_fix_prompt_after_id = self._root.after(delay_ms, _fire)

    def _reset_ort_fix_prompt_state(self, clear_history: bool = False):
        if self._ort_fix_prompt_after_id is not None:
            try:
                self._root.after_cancel(self._ort_fix_prompt_after_id)
            except Exception:
                pass
            self._ort_fix_prompt_after_id = None
        self._ort_fix_prompt_pending = False
        self._ort_fix_prompt_active = False
        if clear_history:
            self._ort_fix_prompt_shown_keys.clear()

    def _prompt_ort_fix(self, issue_key: str = "gpu_runtime_fallback"):
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            return
        if self._ort_fix_prompt_active:
            return
        if self._get_is_processing():
            self._schedule_ort_fix_prompt(issue_key=issue_key, delay_ms=5000)
            return
        self._ort_fix_prompt_active = True
        self._ort_fix_prompt_shown_keys.add(issue_key)
        self.log(f"ℹ️ [PROMPT] 顯示修復提示: {issue_key}")
        try:
            answer = messagebox.askyesno(
                "建議修復 AI 組件",
                "偵測到 AI 組件版本與您的系統不符（GPU 版裝在無 NVIDIA 顯示卡的電腦上）。\n\n"
                "目前程式已自動切換至 CPU 模式，音訊分離功能仍可正常使用。\n\n"
                "建議執行修復以取得最佳效能並避免每次啟動的診斷延遲。\n"
                "（重新下載適合的 CPU 版本，約 800MB）\n\n"
                "是否立即自動修復？",
            )
            if answer:
                self._start_setup("auto")
            else:
                self._ort_fix_prompt_suppressed_keys.add(issue_key)
                self.log(f"ℹ️ [PROMPT] 使用者已拒絕本次修復提示: {issue_key}")
        finally:
            self._ort_fix_prompt_active = False

    # ------------------------------------------------------------------
    # Environment setup (run in background thread via runner.start())
    # ------------------------------------------------------------------

    def async_setup_environment(self, install_mode: str = "auto"):
        self.log(f"--- 開始自動化環境部署 (模式: {install_mode}) ---")

        setup_dirs = [
            ("音訊引擎", self.bin_dir),
            ("Python環境", self.py_dir),
            ("CPU AI函式庫", self.lib_dir),
            ("GPU AI函式庫", self.gpu_lib_dir),
            ("模型目錄", self.models_dir),
        ]
        for name, d in setup_dirs:
            try:
                if not d.parent.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                d.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 目錄已就緒: {d.name}")
            except Exception as e:
                self.log(f"❌ 無法建立 {name} 目錄: {d}")
                self.log(f"   錯誤訊息: {str(e)}")
                return  # runner._finish() handles cleanup

        if not self.local_python.exists():
            self.log("🚀 正在下載內建 Python 核心 (約 10MB)...")
            if not self.download_portable_python():
                self.log("❌ Python 下載失敗，請檢查網路連線。")
                return
        else:
            self.fix_python_pth()

        if self.local_python.exists():
            self.log("✅ 內建 Python 核心已就緒。")
        else:
            self.log("❌ Python 部署異常：路徑存在但找不到執行檔。")
            return

        if not (self.bin_dir / "ffmpeg.exe").is_file():
            self.log("🚀 正在下載音訊引擎 FFmpeg (約 100MB+)...")
            if not self.download_ffmpeg():
                self.log("❌ FFmpeg 下載失敗。")
        else:
            self.log("✅ 音訊引擎已就緒。")

        self.log("🔍 正在進行 AI 運算環境深度檢查...")
        packages_ok = False
        target_ai_dir = self._get_target_ai_dir(install_mode)
        expect_gpu_stack = (target_ai_dir == self.gpu_lib_dir)

        has_torch = (target_ai_dir / "torch").exists()
        has_sep = (target_ai_dir / "audio_separator").exists()
        has_ort = self._has_onnxruntime_package(target_ai_dir)

        if has_torch and has_sep and has_ort:
            try:
                self.log(f"  > 正在測試 {'GPU' if expect_gpu_stack else 'CPU'} 組件導入...")
                check_cmd = f"""
import sys, os, subprocess
sys.path.insert(0, r'{target_ai_dir}')
if {str(expect_gpu_stack)} and hasattr(os, 'add_dll_directory'):
    lib_path = r'{target_ai_dir}'
    for root_dir, dirs, files in os.walk(lib_path):
        for sub in ['bin', 'lib']:
            p = os.path.join(root_dir, sub)
            if os.path.isdir(p):
                try: os.add_dll_directory(p)
                except Exception: pass

has_nvidia_gpu = False
try:
    r = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, timeout=8)
    if r.returncode == 0 and r.stdout.strip():
        has_nvidia_gpu = True
except Exception:
    pass

ort_import_ok = False
torch_import_ok = False
ort_err = ''

try:
    import onnxruntime as ort
    ort_import_ok = True
except ImportError as e:
    ort_err = str(e)
except Exception as e:
    ort_err = str(e)
    if 'DLL' in ort_err or 'dll' in ort_err or 'initialization routine' in ort_err or '初始化' in ort_err:
        if not has_nvidia_gpu:
            print('CHECK_RESULT:WRONG_BUILD_FOR_CPU')
        else:
            print(f'CHECK_RESULT:ORT_DLL_FAIL_{{ort_err[:80]}}')
        import sys; sys.exit(0)

try:
    import torch
    torch_import_ok = True
except ImportError as e:
    print(f'CHECK_RESULT:MISSING_torch_{{str(e)}}')
    import sys; sys.exit(0)
except Exception as e:
    print(f'CHECK_RESULT:ERROR_torch_{{str(e)}}')
    import sys; sys.exit(0)

if not ort_import_ok:
    print(f'CHECK_RESULT:MISSING_ort_{{ort_err}}')
else:
    providers = ort.get_available_providers()
    cuda_available = torch.cuda.is_available()
    sm_compatible = True
    if cuda_available:
        try: torch.zeros(1).cuda()
        except Exception as e:
            if 'sm_120' in str(e) or 'sm_' in str(e): sm_compatible = False
    cuda_ok = 'CUDAExecutionProvider' in providers and cuda_available and sm_compatible
    is_cpu_build = '+cpu' in torch.__version__
    if cuda_ok:
        print('CHECK_RESULT:OK')
    elif not sm_compatible:
        print('CHECK_RESULT:SM120_INCOMPATIBLE')
    elif not has_nvidia_gpu and is_cpu_build:
        print('CHECK_RESULT:CPU_OK')
    elif not has_nvidia_gpu and not is_cpu_build:
        print('CHECK_RESULT:WRONG_BUILD_FOR_CPU')
    else:
        print(f'CHECK_RESULT:NO_CUDA providers={{providers}} cuda={{cuda_available}}')
"""
                env = self.build_python_env(target_ai_dir, include_gpu_runtime=expect_gpu_stack)
                res = subprocess.run(
                    [str(self.local_python), "-c", check_cmd],
                    capture_output=True, text=True, creationflags=self.subp_flags,
                    env=env, timeout=60, encoding="utf-8", errors="replace",
                )
                check_out = res.stdout.strip() if res.stdout else ""
                self.log(f"  > 核心組件狀態: {check_out}")

                if "CHECK_RESULT:OK" in check_out:
                    packages_ok = True
                elif "CHECK_RESULT:CPU_OK" in check_out:
                    self.log("✅ 無 NVIDIA 顯示卡，CPU 版本組件運作正常。")
                    packages_ok = True
                elif "CHECK_RESULT:SM120_INCOMPATIBLE" in check_out:
                    self.log("🔍 偵測到 RTX 50 系列顯示卡與現有運算核心不相容，將執行強制升級。")
                elif "CHECK_RESULT:WRONG_BUILD_FOR_CPU" in check_out:
                    self.log("🔍 偵測到安裝的是 GPU 版本但主機無 NVIDIA 顯示卡，將重裝為 CPU 版本。")
                else:
                    self.log("🔍 偵測到加速組件不完整或不支援 GPU，將執行修復。")
            except Exception as e:
                self.log(f"⚠️ 檢查過程發生異常: {str(e)}")
        else:
            if not has_torch:
                self.log("🔍 偵測到缺少 PyTorch 核心組件。")
            if not has_sep:
                self.log("🔍 偵測到缺少音訊分離核心組件。")
            if not has_ort:
                self.log("🔍 偵測到缺少 ONNX Runtime 核心。")

        if not packages_ok or install_mode != "auto":
            self.log(f"🚀 準備執行 AI 運算組件安裝/修復 (模式: {install_mode})...")
            if not self.install_packages_locally(install_mode=install_mode):
                self.log("❌ AI 組件安裝失敗，請查看上方詳細日誌。")
                return
            self.log("✅ AI 組件安裝/修復完成。")
            self.log("🔄 正在根據新環境自動切換運算裝置...")
            self._startup_ort_check()
        else:
            self.log("✅ AI 運算組件已就緒。")

        if not self._is_ytdlp_installed():
            self.log("🚀 正在補齊 YouTube 下載組件 (yt-dlp)...")
            try:
                result = subprocess.run(
                    [str(self.local_python), "-m", "pip", "install", "--upgrade", "yt-dlp",
                     "--target", str(self.lib_dir), "--no-warn-script-location"],
                    creationflags=self.subp_flags, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=180,
                )
                if result.returncode == 0:
                    self.log("✅ YouTube 下載組件已就緒。")
                else:
                    err_msg = (result.stderr or result.stdout or "").strip()[:300]
                    self.log(f"⚠️ YouTube 下載組件安裝失敗: {err_msg}")
            except subprocess.TimeoutExpired:
                self.log("⚠️ YouTube 下載組件安裝逾時，請稍後再試。")
            except Exception as e:
                self.log(f"⚠️ YouTube 下載組件安裝失敗: {str(e)}")
        else:
            self.log("✅ YouTube 下載組件已就緒。")

        self._progress(100, "全部就緒")
        self._reset_ort_fix_prompt_state(clear_history=True)
        self._startup_component_prompt_shown = False
        self.log("--- 環境部署完成 ---")
        # runner._finish() handles is_running cleanup

    # ------------------------------------------------------------------
    # check_components (main-thread only — called via root.after or button)
    # ------------------------------------------------------------------

    def check_components(self, prompt: bool = True):
        has_gpu = self._is_nvidia_gpu_present()
        target_ai_dir = self._get_target_ai_dir("auto")
        cpu_ready = (
            (self.lib_dir / "torch").exists()
            and (self.lib_dir / "audio_separator").exists()
            and self._has_onnxruntime_package(self.lib_dir)
        )
        gpu_ready = (
            (self.gpu_lib_dir / "torch").exists()
            and (self.gpu_lib_dir / "audio_separator").exists()
            and self._has_onnxruntime_package(self.gpu_lib_dir)
        )
        missing = []
        if not self.local_python.exists():
            missing.append("內建 Python 核心")
        if not (self.bin_dir / "ffmpeg.exe").exists():
            missing.append("音訊引擎 FFmpeg")
        if not (target_ai_dir / "torch").exists():
            missing.append("PyTorch 核心")
        if not (target_ai_dir / "audio_separator").exists():
            missing.append("AI 音訊分離組件")
        if not self._has_onnxruntime_package(target_ai_dir):
            missing.append("ONNX Runtime 核心")

        startup_missing = []
        if not self.local_python.exists():
            startup_missing.append("內建 Python 核心")
        if not (self.bin_dir / "ffmpeg.exe").exists():
            startup_missing.append("音訊引擎 FFmpeg")
        startup_ai_ready = gpu_ready if has_gpu and gpu_ready else cpu_ready

        if not prompt and not startup_missing and startup_ai_ready:
            self._check_ytdlp()
            threading.Thread(target=self._startup_ort_check, daemon=True).start()
            return

        if not prompt:
            if self._startup_component_prompt_shown:
                self.log("ℹ️ 啟動修復提示本次已顯示過，略過重複彈窗。")
                return
            self._startup_component_prompt_shown = True

        install_mode = "auto"

        if not has_gpu:
            msg = "偵測到您的電腦未安裝或未啟用 NVIDIA 顯示卡。\n\n"
            if missing:
                msg += f"目前缺少組件：{'、'.join(missing)}\n\n"
            msg += "是否立即安裝相容性最高的 CPU 版本？(約 800MB)"
            if messagebox.askyesno("環境部署確認", msg):
                install_mode = "cpu"
            else:
                if missing:
                    self.log("💡 提示：環境不完整，建議稍後點擊「初始化/修復環境」。")
                return
        else:
            dialog = tk.Toplevel(self._root)
            dialog.title("環境初始化 / 修復")
            dialog.geometry("450x320")
            dialog.transient(self._root)
            dialog.grab_set()

            dialog.update_idletasks()
            x = self._root.winfo_x() + (self._root.winfo_width() - dialog.winfo_width()) // 2
            y = self._root.winfo_y() + (self._root.winfo_height() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")

            tk.Label(dialog, text="請選擇環境部署模式", font=("Arial", 12, "bold")).pack(pady=10)
            desc_text = (
                "• 自動偵測 (推薦)：根據您的顯示卡自動選擇最佳版本。\n"
                "• CPU + GPU 雙支援：分開安裝 CPU 與 GPU 兩套核心，互不干擾。\n"
                "• 僅安裝 CPU 版：最省空間，相容所有電腦，無加速 (約 800MB)。"
            )
            tk.Label(dialog, text=desc_text, justify=tk.LEFT, fg="#666").pack(pady=5, padx=20)

            mode_var = tk.StringVar(value="auto")
            tk.Radiobutton(dialog, text="自動偵測 (根據顯卡自動配置)", variable=mode_var, value="auto").pack(anchor=tk.W, padx=50, pady=2)
            tk.Radiobutton(dialog, text="安裝 CPU + GPU 雙支援版 (最彈性)", variable=mode_var, value="both").pack(anchor=tk.W, padx=50, pady=2)
            tk.Radiobutton(dialog, text="僅安裝 GPU 加速版 (需 NVIDIA 顯卡)", variable=mode_var, value="gpu").pack(anchor=tk.W, padx=50, pady=2)
            tk.Radiobutton(dialog, text="僅安裝 CPU 穩定版 (相容性最高)", variable=mode_var, value="cpu").pack(anchor=tk.W, padx=50, pady=2)

            result = {"action": "cancel"}

            def on_confirm():
                result["action"] = "confirm"
                result["mode"] = mode_var.get()
                dialog.destroy()

            btn_frame = tk.Frame(dialog)
            btn_frame.pack(pady=20)
            tk.Button(btn_frame, text="開始下載並安裝", command=on_confirm, bg="#4CAF50", fg="white", width=15).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=10)

            self._root.wait_window(dialog)
            if result["action"] != "confirm":
                if missing:
                    self.log("已取消環境初始化。")
                return
            install_mode = result["mode"]

        self._start_setup(install_mode)

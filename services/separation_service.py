import os
import re
import subprocess
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from services.task_result import TaskResult

if TYPE_CHECKING:
    from services.environment_service import EnvironmentService
    from services.ffmpeg_service import FfmpegService


class SeparationService:
    def __init__(
        self,
        local_python: Path,
        models_dir: Path,
        log_fn: Callable,
        cancel_event,
        subp_flags: int,
        env_service: "EnvironmentService",
        ffmpeg_svc: "FfmpegService",
    ):
        self.local_python = local_python
        self.models_dir = models_dir
        self.log = log_fn
        self.cancel_event = cancel_event
        self.subp_flags = subp_flags
        self._env = env_service
        self._ffmpeg = ffmpeg_svc
        self._current_process = None

    @staticmethod
    def _sanitize_filename(stem: str, max_len: int = 60) -> str:
        illegal = r'\/:*?"<>|'
        for ch in illegal:
            stem = stem.replace(ch, "_")
        stem = "".join(char for char in stem if char.isprintable())
        stem = re.sub(r"[\s_]+", "_", stem).strip("_")
        if len(stem) > max_len:
            stem = stem[:max_len]
        while len(stem.encode("utf-8", errors="replace")) > 180:
            stem = stem[:-1]
        return stem.strip() or "audio"

    def run_audio_separator(
        self,
        input_file: str,
        output_dir: str,
        fmt: str,
        device_str: str,
        model: str,
        overlap: float,
        denoise: bool,
    ) -> TaskResult:
        self._env.fix_python_pth()

        device = "cuda" if device_str == "gpu" else "cpu"
        runtime_ready, device, runtime_lib_dir = self._env.ensure_runtime_stack_ready(device)
        if not runtime_ready:
            return TaskResult(success=False, error="runtime_not_ready")

        env = self._env.build_python_env(runtime_lib_dir, include_gpu_runtime=(device == "cuda"))
        script = f"""
import sys, os
sys.path.insert(0, r'{runtime_lib_dir}')

if {str(device == "cuda")} and hasattr(os, 'add_dll_directory'):
    lib_path = r'{runtime_lib_dir}'
    for root, dirs, files in os.walk(lib_path):
        for sub in ['bin', 'lib']:
            p = os.path.join(root, sub)
            if os.path.isdir(p):
                try: os.add_dll_directory(p)
                except Exception: pass

from audio_separator.utils.cli import main
main()
"""
        selected_model = model.split(" ")[0]
        fallback_model = "htdemucs.yaml"

        def build_command(model_name):
            is_demucs = model_name.endswith(".yaml")
            command = [
                str(self.local_python), "-c", script,
                input_file,
                "-m", model_name,
                "--model_file_dir", str(self.models_dir),
                "--output_dir", output_dir,
                "--output_format", fmt,
                "--output_bitrate", "320k",
                "--normalization", "0.9",
            ]
            if is_demucs:
                command.extend([
                    "--demucs_segment_size", "None",
                    "--demucs_shifts", "2",
                    "--demucs_overlap", "0.25",
                ])
            else:
                command.extend([
                    "--mdx_overlap", str(overlap),
                    "--mdx_segment_size", "256",
                    "--mdx_hop_length", "1024",
                ])
                if denoise:
                    command.append("--mdx_enable_denoise")
            if device == "cuda":
                command.append("--use_autocast")
                if not is_demucs:
                    command.extend(["--mdx_batch_size", "4"])
            else:
                if not is_demucs:
                    command.extend(["--mdx_batch_size", "1"])
            return command, is_demucs

        def has_output_files():
            input_stem = Path(input_file).stem
            out_path = Path(output_dir)
            keywords = ["_(Vocals)", "_(Instrumental)", "_(No Vocals)",
                        "_(Bass)", "_(Drums)", "_(Other)"]
            try:
                for fname in os.listdir(str(out_path)):
                    if fname.startswith(input_stem) and fname.endswith(f".{fmt}"):
                        if any(kw in fname for kw in keywords):
                            return True
            except Exception:
                pass
            return False

        def run_model(model_name):
            command, is_demucs = build_command(model_name)
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding="utf-8", errors="replace",
                    creationflags=self.subp_flags, env=env,
                )
                self._current_process = process

                gpu_kernel_error = False
                unsupported_model_error = False
                unsupported_model_hash = None

                for line in process.stdout:
                    if self.cancel_event.is_set():
                        process.terminate()
                        self.log("🛑 AI 分離任務已取消。")
                        return False, "cancelled"
                    line = line.strip()
                    if line:
                        self.log(line)
                        if "no kernel image is available" in line:
                            gpu_kernel_error = True
                        if "Unsupported Model File: parameters for MD5 hash" in line:
                            unsupported_model_error = True
                            hash_match = re.search(r"MD5 hash ([0-9a-fA-F]{32})", line)
                            if hash_match:
                                unsupported_model_hash = hash_match.group(1)

                process.wait()
                self._current_process = None

                if process.returncode == 0 and not gpu_kernel_error and not unsupported_model_error:
                    if has_output_files():
                        return True, "success"
                    self.log("❌ 雖然程式回報成功，但未能在輸出目錄找到產出的音檔。")
                    return False, "missing_output"

                if gpu_kernel_error:
                    self.log("\n❌ 偵測到 GPU 核心錯誤 (no kernel image)。")
                    self.log("💡 這通常是因為您的 GPU 太新 (RTX 50 系列)，目前的穩定版組件尚未完全支援。")
                    self.log("💡 建議：請在主介面將「運算裝置」切換為 CPU 模式運行，或嘗試執行「一鍵修復」升級至最新實驗性核心。")
                    return False, "gpu_kernel"

                if unsupported_model_error:
                    model_path = self.models_dir / model_name
                    self.log("\n❌ 偵測到 UVR 模型參數不相容。")
                    if unsupported_model_hash:
                        self.log(f"💡 此模型的 MD5 雜湊值為: {unsupported_model_hash}")
                    self.log(f"💡 目前模型 `{model_name}` 的內容不在 audio-separator 內建支援表中。")
                    if model_path.exists():
                        self.log(f"💡 建議刪除後重新下載模型檔: {model_path}")
                    else:
                        self.log("💡 這通常代表模型檔下載不完整、版本不相容，或內容已被替換。")
                    if not is_demucs and model_name != fallback_model:
                        if not is_retry:
                            self.log(f"🔁 將自動改用 `{fallback_model}` 再重試一次...")
                            return False, "retry_with_demucs"
                    else:
                        self.log("💡 可改用 `htdemucs.yaml` 或 `htdemucs_ft.yaml` 進行分離。")
                    return False, "unsupported_model"

                return False, "process_failed"
            except Exception as e:
                self.log(f"執行錯誤: {str(e)}")
                self._current_process = None
                return False, "exception"

        success, reason = run_model(selected_model)
        if success:
            return TaskResult(success=True)

        if reason == "retry_with_demucs":
            self.log(f"🎯 回退模型: {selected_model} → {fallback_model}")
            retry_success, retry_reason = run_model(fallback_model)
            if retry_success:
                self.log(f"✅ 已改用 `{fallback_model}` 完成音訊分離。")
                self.log("💡 若想恢復使用原本的 MDX 模型，請刪除舊的 .onnx 後重新下載。")
                return TaskResult(success=True)
            if retry_reason == "unsupported_model":
                self.log("❌ 備援模型也無法載入，請執行「一鍵修復/初始化環境」，或手動清理模型目錄後再試。")
            else:
                self.log("❌ 已嘗試自動切換備援模型，但仍未成功完成分離。")

        return TaskResult(success=False, error=reason)

    def consolidate_stems(
        self, input_audio: str, reference_video: str, output_dir: str, fmt: str
    ) -> tuple[str | None, str | None]:
        audio_stem = Path(input_audio).stem
        video_stem = Path(reference_video).stem
        out_path = Path(output_dir)

        safe_stem = self._sanitize_filename(video_stem, max_len=60)
        voc_final = out_path / f"{safe_stem}_vocals.{fmt}"
        inst_final = out_path / f"{safe_stem}_instrumental.{fmt}"

        # 1. 尋找人聲
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and "(Vocals)" in f.name and f.suffix == f".{fmt}":
                if voc_final.exists():
                    os.remove(voc_final)
                f.rename(voc_final)
                break

        # 2. 尋找伴奏 (MDX 模式)
        found_inst = False
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and any(x in f.name for x in ["(Instrumental)", "(No Vocals)"]) and f.suffix == f".{fmt}":
                if inst_final.exists():
                    os.remove(inst_final)
                f.rename(inst_final)
                found_inst = True
                break

        # 3. 如果沒找到伴奏，檢查是否為 Demucs 多音軌模式
        if not found_inst:
            stems_to_merge = []
            tags = [
                "(Bass)", "(Drums)", "(Other)", "(Guitar)", "(Piano)",
                "_Bass", "_Drums", "_Other", "_Guitar", "_Piano",
            ]
            for f in out_path.iterdir():
                if f.name.startswith(audio_stem) and any(tag in f.name for tag in tags) and f.suffix == f".{fmt}":
                    stems_to_merge.append(f)

            if stems_to_merge:
                stem_names = ", ".join(sorted(f.name for f in stems_to_merge))
                self.log(f"  > 偵測到 Demucs 多音軌，正在合併 {len(stems_to_merge)} 個音軌為伴奏...")
                self.log(f"  > 參與合併的音軌: {stem_names}")
                result = self._ffmpeg.merge_stems([str(f) for f in stems_to_merge], str(inst_final))
                if result.success:
                    found_inst = True
                else:
                    self.log(f"  ❌ 合併音軌失敗: {result.error}")

        # 4. 清理暫存檔
        self.log("🧹 正在清理暫存檔案...")
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem):
                try:
                    f.unlink()
                except Exception as e:
                    self.log(f"  ⚠️ 清理暫存檔失敗: {f.name} ({str(e)})")
        if Path(input_audio).exists():
            try:
                os.remove(input_audio)
            except Exception as e:
                self.log(f"  ⚠️ 清理原始音檔失敗: {str(e)}")

        return (
            str(voc_final) if voc_final.exists() else None,
            str(inst_final) if inst_final.exists() else None,
        )

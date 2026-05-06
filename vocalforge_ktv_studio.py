import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import subprocess
import threading
from pathlib import Path
import urllib.request
import zipfile
import shutil
import multiprocessing
import re
import ssl
import json
from services.task_result import TaskResult
from services.task_runner import TaskRunner

APP_NAME = "VocalForge KTV Studio"
APP_VERSION = "2.10.1"
APP_DESCRIPTION = "專業人聲分離、YouTube 下載與 KTV 伴唱製作工作台"

# 防止 EXE 遞迴啟動
if __name__ == "__main__":
    multiprocessing.freeze_support()  # 已處理 --multiprocessing-fork worker，worker 在此之後會 sys.exit
    if '--smoke-test' in sys.argv:
        sys.exit(0)
    # 其他額外參數（未來 CLI 支援、拖曳檔案）不強制退出，讓 GUI 正常開啟

class VocalForgeStudioApp:
    def __init__(self, root):
        self.root = root
        self.version = APP_VERSION
        self.root.title(f"{APP_NAME} {self.version}")
        self.root.geometry("880x750")
        
        # 取得執行路徑 (EXE 所在目錄)
        # 注意：PyInstaller onefile 模式下 sys.executable 指向 EXE 本身（正確）
        # 但部分版本 sys._MEIPASS 指向暫存解壓目錄，必須用 sys.executable 而非 __file__
        if getattr(sys, 'frozen', False):
            exe_path = Path(sys.executable)
            # 若 EXE 被放在 Temp 目錄（代表是 onefile 解壓中間狀態），改用環境變數
            if 'Temp' in str(exe_path) or 'temp' in str(exe_path):
                # 嘗試從 _MEIPASS 的父目錄推算（onefile 解壓時 sys.executable 仍正確）
                # 這種情況實際上不應發生，但作為保護
                import os as _os
                alt = _os.environ.get('_MEIPASS2', '') or _os.environ.get('PYINSTALLER_ORIG_EXEC', '')
                if alt:
                    self.app_dir = Path(alt).parent
                else:
                    self.app_dir = exe_path.parent
            else:
                self.app_dir = exe_path.parent
        else:
            self.app_dir = Path(__file__).parent
            
        # 自動遷移舊資料夾名稱 (與 gui_app_2.py 保持一致)
        migrations = {
            "bin": "engine_ffmpeg",
            "python_env": "runtime_python",
            "packages": "ai_libraries",
            "packages_gpu": "ai_libraries_gpu",
            "models": "ai_models"
        }
        for old_name, new_name in migrations.items():
            old_p = self.app_dir / old_name
            new_p = self.app_dir / new_name
            if old_p.exists() and not new_p.exists():
                try:
                    old_p.rename(new_p)
                except Exception as e:
                    pass  # 資料夾遷移失敗不中斷啟動，通常是權限問題

        # 定義全外部目錄 (與 gui_app_2.py 完全共用)
        self.bin_dir = self.app_dir / "engine_ffmpeg"      # 存放音訊引擎 (FFmpeg)
        self.py_dir = self.app_dir / "runtime_python"      # 存放內建 Python 核心
        self.lib_dir = self.app_dir / "ai_libraries"       # CPU AI 組件 + 共用 Python 套件
        self.gpu_lib_dir = self.app_dir / "ai_libraries_gpu"  # GPU AI 組件（與 CPU 完全分離）
        self.models_dir = self.app_dir / "ai_models"       # 存放 AI 分離模型
        
        for d in [self.bin_dir, self.py_dir, self.lib_dir, self.gpu_lib_dir, self.models_dir]:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                pass  # 建立失敗不中斷啟動，後續操作會再次嘗試
        
        # 內建 Python 的路徑
        self.local_python = self.py_dir / "python.exe"
        
        # 設定環境變數與 DLL 載入路徑
        os.environ["PATH"] = f"{self.bin_dir}{os.pathsep}{self.py_dir}{os.pathsep}{os.environ['PATH']}"
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(str(self.bin_dir))
            except Exception as e:
                pass  # Windows 版本過舊或路徑無效時忽略
        
        os.environ["PYTHONPATH"] = str(self.lib_dir)
        
        self.subp_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.file_list = []
        self.is_processing = False
        # runner 統一管理任務生命週期；cancel_event 與 runner 共享同一 Event 物件
        self.runner = TaskRunner(self.root, self.log, self.update_progress, self.update_status)
        self.cancel_event = self.runner.cancel_event
        self._current_process = None            # 追蹤當前子程序，供取消使用
        self._ort_fix_prompt_after_id = None
        self._ort_fix_prompt_pending = False
        self._ort_fix_prompt_active = False
        self._ort_fix_prompt_shown_keys = set()
        self._ort_fix_prompt_suppressed_keys = set()
        self._startup_component_prompt_shown = False
        self._startup_ort_check_running = False
        self._last_downloaded_subtitle = None
        self._yt_js_runtime_cache = None
        self._yt_js_runtime_notice_shown = False
        self.yt_url_var = tk.StringVar()
        self.setup_ui()
        
        # 啟動時自動檢查
        self.root.after(500, lambda: self.check_components(prompt=False))

    def setup_ui(self):
        tk.Label(self.root, text=f"{APP_NAME} {self.version}", font=("Arial", 16, "bold")).pack(pady=10)
        tk.Label(self.root, text=APP_DESCRIPTION, fg="#555", font=("Arial", 9)).pack(pady=(0, 6))
        
        # 使用 Notebook 分組功能
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(pady=5, fill=tk.BOTH, padx=20, expand=False)
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        # --- Tab 1: YouTube 轉 MKV ---
        yt_tab = tk.Frame(self.notebook, padx=10, pady=10)
        self.notebook.add(yt_tab, text=" 📺 YouTube 一鍵轉 KTV ")
        
        tk.Label(yt_tab, text="YouTube 網址:").pack(side=tk.LEFT)
        self.yt_entry = tk.Entry(yt_tab, textvariable=self.yt_url_var)
        self.yt_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_entry.bind("<Button-1>", self.quick_paste_url)
        
        # 提示標籤
        tk.Label(yt_tab, text="(點擊輸入框自動貼上剪貼簿網址)", fg="gray", font=("Arial", 8)).pack(side=tk.BOTTOM, anchor=tk.W, padx=(85, 0))

        # --- Tab 2: YouTube 純下載 ---
        yt_dl_tab = tk.Frame(self.notebook, padx=10, pady=5)
        self.notebook.add(yt_dl_tab, text=" 📥 YouTube 下載 (MP3/MP4) ")

        # 第一列：網址輸入（Tab 2 獨立變數，不影響 Tab 1）
        dl_url_row = tk.Frame(yt_dl_tab)
        dl_url_row.pack(fill=tk.X, pady=2)
        tk.Label(dl_url_row, text="YouTube 網址:").pack(side=tk.LEFT)
        self.yt_dl_url_var = tk.StringVar()
        self.yt_dl_entry = tk.Entry(dl_url_row, textvariable=self.yt_dl_url_var)
        self.yt_dl_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_dl_entry.bind("<Button-1>", self.quick_paste_dl_url)
        tk.Label(dl_url_row, text="(點擊自動貼上)", fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

        # 第二列：下載格式 + 畫質選擇
        dl_opt_row = tk.Frame(yt_dl_tab)
        dl_opt_row.pack(fill=tk.X, pady=2)

        tk.Label(dl_opt_row, text="下載格式:").pack(side=tk.LEFT)
        self.dl_type_var = tk.StringVar(value="both")
        tk.Radiobutton(dl_opt_row, text="MP3 + MP4", variable=self.dl_type_var, value="both").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="僅 MP3",    variable=self.dl_type_var, value="mp3" ).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="僅 MP4",    variable=self.dl_type_var, value="mp4" ).pack(side=tk.LEFT, padx=4)

        tk.Label(dl_opt_row, text="  |  MP4 畫質:").pack(side=tk.LEFT, padx=(10, 0))
        self.dl_quality_var = tk.StringVar(value="1080")
        tk.Radiobutton(dl_opt_row, text="最佳",  variable=self.dl_quality_var, value="best" ).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="1080p", variable=self.dl_quality_var, value="1080" ).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="720p",  variable=self.dl_quality_var, value="720"  ).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="480p",  variable=self.dl_quality_var, value="480"  ).pack(side=tk.LEFT, padx=4)

        # 第三列：是否進行音訊分離（Checkbox）
        dl_sep_toggle_row = tk.Frame(yt_dl_tab)
        dl_sep_toggle_row.pack(fill=tk.X, pady=(4, 0))
        self.dl_do_separate_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dl_sep_toggle_row,
            text="下載後進行 AI 音訊分離（人聲／伴奏）",
            variable=self.dl_do_separate_var,
            command=self._toggle_dl_separate_options,
            font=("Arial", 9, "bold")
        ).pack(side=tk.LEFT)

        # 音訊分離選項區（預設隱藏）
        self.dl_sep_options_frame = tk.LabelFrame(yt_dl_tab, text="音訊分離設定", padx=8, pady=4)
        # 不在這裡 pack，由 _toggle_dl_separate_options 控制顯示

        # 分離選項第一列：輸出格式
        sep_fmt_row = tk.Frame(self.dl_sep_options_frame)
        sep_fmt_row.pack(fill=tk.X, pady=2)
        tk.Label(sep_fmt_row, text="分離輸出格式:").pack(side=tk.LEFT)
        self.dl_sep_format_var = tk.StringVar(value="mp3")
        for fmt_val in ["mp3", "wav", "flac"]:
            tk.Radiobutton(sep_fmt_row, text=fmt_val.upper(),
                           variable=self.dl_sep_format_var, value=fmt_val).pack(side=tk.LEFT, padx=8)

        # 分離選項第二列：分離模型
        sep_model_row = tk.Frame(self.dl_sep_options_frame)
        sep_model_row.pack(fill=tk.X, pady=2)
        tk.Label(sep_model_row, text="分離模型:").pack(side=tk.LEFT)
        self.dl_sep_model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        dl_model_options = [
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏優化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品質綜合)",
            "Kim_Vocal_2.onnx (MDX - 極致人聲提取)",
            "htdemucs.yaml (Demucs - 4音軌高品質分離)",
            "htdemucs_ft.yaml (Demucs - 流行樂優化)",
        ]
        ttk.Combobox(sep_model_row, textvariable=self.dl_sep_model_var,
                     values=dl_model_options, state="readonly", width=42).pack(side=tk.LEFT, padx=5)

        # --- Tab 3: 本地影片轉 MKV ---
        local_v_tab = tk.Frame(self.notebook, padx=10, pady=10)
        self.notebook.add(local_v_tab, text=" 🎬 本地影片轉 KTV ")
        
        v_list_frame = tk.Frame(local_v_tab)
        v_list_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        self.v_listbox = tk.Listbox(v_list_frame, height=5, selectmode=tk.EXTENDED)
        self.v_listbox.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        v_scrollbar = tk.Scrollbar(v_list_frame)
        v_scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.v_listbox.config(yscrollcommand=v_scrollbar.set)
        v_scrollbar.config(command=self.v_listbox.yview)
        
        v_btn_frame = tk.Frame(local_v_tab)
        v_btn_frame.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        self.v_list =[] # 儲存影片檔案路徑
        tk.Button(v_btn_frame, text="加入影片", command=self.browse_local_video, width=10).pack(pady=2)
        tk.Button(v_btn_frame, text="加入資料夾", command=self.browse_local_v_folder, width=10).pack(pady=2)
        tk.Button(v_btn_frame, text="移除選取", command=self.remove_selected_v, width=10).pack(pady=2)
        tk.Button(v_btn_frame, text="清除清單", command=self.clear_v_list, width=10).pack(pady=2)

        # --- Tab 4: 本地檔案分離 ---
        file_tab = tk.Frame(self.notebook, padx=10, pady=10)
        self.notebook.add(file_tab, text=" 🎵 本地音檔批量分離 ")
        
        file_list_frame = tk.Frame(file_tab)
        file_list_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        self.file_listbox = tk.Listbox(file_list_frame, height=5, selectmode=tk.EXTENDED)
        self.file_listbox.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        scrollbar = tk.Scrollbar(file_list_frame)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.file_listbox.yview)
        
        file_btn_frame = tk.Frame(file_tab)
        file_btn_frame.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        tk.Button(file_btn_frame, text="加入檔案", command=self.browse_file, width=10).pack(pady=2)
        tk.Button(file_btn_frame, text="移除選取", command=self.remove_selected_file, width=10).pack(pady=2)
        tk.Button(file_btn_frame, text="清除清單", command=self.clear_files, width=10).pack(pady=2)
        
        # --- 設定區 (簡化版) ---
        settings_frame = tk.LabelFrame(self.root, text="核心設定", padx=10, pady=10)
        settings_frame.pack(pady=5, fill=tk.X, padx=20)
        
        # 輸出目錄
        out_row = tk.Frame(settings_frame)
        out_row.pack(fill=tk.X, pady=2)
        self.output_dir_var = tk.StringVar(value=str(self.app_dir / "output"))
        tk.Label(out_row, text="輸出目錄:").pack(side=tk.LEFT)
        tk.Entry(out_row, textvariable=self.output_dir_var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        tk.Button(out_row, text="瀏覽", command=self.browse_output_dir).pack(side=tk.RIGHT)
        
        # Cookie 設定列（防止 YouTube 429 封鎖）
        cookie_row = tk.Frame(settings_frame)
        cookie_row.pack(fill=tk.X, pady=2)
        tk.Label(cookie_row, text="YouTube Cookie:").pack(side=tk.LEFT)
        self.cookie_browser_var = tk.StringVar(value="none")
        tk.Radiobutton(cookie_row, text="不使用", variable=self.cookie_browser_var, value="none").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Chrome", variable=self.cookie_browser_var, value="chrome").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Firefox", variable=self.cookie_browser_var, value="firefox").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Edge", variable=self.cookie_browser_var, value="edge").pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Brave", variable=self.cookie_browser_var, value="brave").pack(side=tk.LEFT, padx=4)
        tk.Label(cookie_row, text="← 遇到 429 封鎖時，選擇你目前登入 YouTube 的瀏覽器", fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=6)

        # 運算裝置與去噪
        self.opt_row = tk.Frame(settings_frame)
        opt_row = self.opt_row
        opt_row.pack(fill=tk.X, pady=5)
        
        tk.Label(opt_row, text="運算裝置:").pack(side=tk.LEFT)
        self.device_var = tk.StringVar(value="cpu")
        tk.Radiobutton(opt_row, text="CPU", variable=self.device_var, value="cpu").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(opt_row, text="GPU (NVIDIA)", variable=self.device_var, value="gpu").pack(side=tk.LEFT, padx=5)
        
        tk.Button(opt_row, text="🔍 檢測 GPU 環境", command=self.check_gpu_env, 
                  font=("Arial", 9), bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=10)
        
        self.denoise_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_row, text="啟用 AI 去噪 (推薦)", variable=self.denoise_var).pack(side=tk.RIGHT, padx=10)

        # 隱藏但保留變數以維持邏輯相容
        self.overlap_var = tk.DoubleVar(value=0.5)
        self.ktv_var = tk.BooleanVar(value=False)
        self.ktv_balance_var = tk.DoubleVar(value=0.0)
        self.ktv_label_var = tk.StringVar(value="平衡 (1.0 : 1.0)")
        self.vocal_mix_var = tk.DoubleVar(value=50)
        self.vocal_mix_label_var = tk.StringVar(value="")
        
        # 影片輸出格式 (MKV/MP4)
        self.video_format_var = tk.StringVar(value="mkv")
        # 音軌模式: "dual" = 雙音軌(伴唱+人聲), "lr" = 左伴唱右人聲
        self.audio_track_mode_var = tk.StringVar(value="dual")
        
        # 輸出格式與模型
        self.format_row = tk.Frame(settings_frame)
        format_row = self.format_row
        format_row.pack(fill=tk.X, pady=2)
        
        tk.Label(format_row, text="AI 模型:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        model_options =[
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏優化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品質綜合)",
            "Kim_Vocal_2.onnx (MDX - 極致人聲提取)",
            "htdemucs.yaml (Demucs - 4音軌高品質分離)",
            "htdemucs_ft.yaml (Demucs - 流行樂優化)",
            "htdemucs_6s.yaml (Demucs - 6音軌擴充版)"
        ]
        self.model_menu = ttk.Combobox(format_row, textvariable=self.model_var, values=model_options, state="readonly", width=45)
        self.model_menu.pack(side=tk.LEFT, padx=5)
        self.model_menu.current(0)
        
        tk.Label(format_row, text="輸出格式:").pack(side=tk.LEFT, padx=(10, 0))
        self.output_format_var = tk.StringVar(value="mp3")
        for fmt in ["mp3", "wav", "flac"]:
            tk.Radiobutton(format_row, text=fmt.upper(), variable=self.output_format_var, value=fmt).pack(side=tk.LEFT, padx=10)

        # KTV 影片設定列
        self.ktv_row = tk.Frame(settings_frame)
        ktv_row = self.ktv_row
        ktv_row.pack(fill=tk.X, pady=2)

        tk.Label(ktv_row, text="KTV 影片格式:").pack(side=tk.LEFT)
        tk.Radiobutton(ktv_row, text="MKV（預設，相容性最佳）",
                       variable=self.video_format_var, value="mkv").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(ktv_row, text="MP4",
                       variable=self.video_format_var, value="mp4").pack(side=tk.LEFT, padx=5)

        # 伴唱帶音軌模式列
        self.track_row = tk.Frame(settings_frame)
        track_row = self.track_row
        track_row.pack(fill=tk.X, pady=2)

        tk.Label(track_row, text="伴唱帶音軌:").pack(side=tk.LEFT)
        tk.Radiobutton(track_row, text="雙音軌（伴唱＋人聲，預設）",
                       variable=self.audio_track_mode_var, value="dual").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(track_row, text="左伴唱／右人聲（單音軌立體聲）",
                       variable=self.audio_track_mode_var, value="lr").pack(side=tk.LEFT, padx=5)

        # 導唱混合比例列
        self.mix_row = tk.Frame(settings_frame)
        mix_row = self.mix_row
        mix_row.pack(fill=tk.X, pady=2)
        tk.Label(mix_row, text="導唱混合比例:").pack(side=tk.LEFT)
        tk.Scale(
            mix_row,
            from_=0, to=100,
            orient=tk.HORIZONTAL,
            showvalue=False,
            resolution=5,
            length=180,
            variable=self.vocal_mix_var,
            command=lambda _value: self.update_vocal_mix_label()
        ).pack(side=tk.LEFT, padx=5)
        tk.Label(mix_row, textvariable=self.vocal_mix_label_var, width=28, anchor="w").pack(side=tk.LEFT, padx=5)
        tk.Label(mix_row, text="人聲越高，越適合跟唱練習", fg="#666").pack(side=tk.LEFT, padx=5)
        self.update_vocal_mix_label()

        # 額外影片選項列
        self.extra_video_row = tk.Frame(settings_frame)
        extra_video_row = self.extra_video_row
        extra_video_row.pack(fill=tk.X, pady=2)

        self.force_1080p_var = tk.BooleanVar(value=False)
        self.force_1080p_chk = tk.Checkbutton(
            extra_video_row,
            text="強制等比輸出 1080p（不足自動補黑邊）",
            variable=self.force_1080p_var
        )
        self.force_1080p_chk.pack(side=tk.LEFT)

        self.yt_cc_var = tk.BooleanVar(value=True)
        self.yt_cc_chk = tk.Checkbutton(
            extra_video_row,
            text="啟用 YouTube CC 字幕處理",
            variable=self.yt_cc_var,
            command=self.refresh_yt_subtitle_mode_ui
        )
        self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))

        self.yt_subtitle_mode_var = tk.StringVar(value="mux")
        self.yt_subtitle_mode_row = tk.Frame(settings_frame)
        tk.Label(self.yt_subtitle_mode_row, text="字幕模式:").pack(side=tk.LEFT)
        tk.Radiobutton(
            self.yt_subtitle_mode_row,
            text="下載SRT字幕",
            variable=self.yt_subtitle_mode_var,
            value="srt_only",
            command=self.refresh_yt_subtitle_mode_ui
        ).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(
            self.yt_subtitle_mode_row,
            text="下載srt字幕並合成",
            variable=self.yt_subtitle_mode_var,
            value="mux",
            command=self.refresh_yt_subtitle_mode_ui
        ).pack(side=tk.LEFT, padx=5)

        # 按鈕區
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=10)
        self.start_btn = tk.Button(btn_frame, text="開始分離任務", command=self.on_start_click, 
                                  bg="#4CAF50", fg="white", font=("Arial", 12, "bold"), width=20)
        self.start_btn.pack(side=tk.LEFT, padx=10)
        self.cancel_btn = tk.Button(btn_frame, text="取消任務", command=self.cancel_processing,
                                   bg="#F44336", fg="white", font=("Arial", 10), width=12, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)
        self.update_btn = tk.Button(btn_frame, text="一鍵修復/初始化環境", command=self.check_components,
                                   bg="#2196F3", fg="white", font=("Arial", 10), width=18)
        self.update_btn.pack(side=tk.LEFT, padx=10)
        
        # 狀態與進度 (移動回 setup_ui 正確位置)
        status_frame = tk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=20)
        self.status_var = tk.StringVar(value="狀態: 初始化中...")
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, fg="blue")
        self.status_label.pack(side=tk.LEFT)
        
        self.progress_text = tk.Label(status_frame, text="0%", font=("Arial", 8))
        self.progress_text.pack(side=tk.RIGHT)
        
        self.progress_bar = ttk.Progressbar(self.root, orient=tk.HORIZONTAL, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=20, pady=5)
        
        # 日誌區
        tk.Label(self.root, text="執行日誌:").pack(anchor=tk.W, padx=20)
        self.log_area = scrolledtext.ScrolledText(self.root, height=12, font=("Consolas", 9), bg="#F8F9FA")
        self.log_area.pack(pady=(5, 10), padx=20, fill=tk.BOTH, expand=True)

        # 立即刷新視窗並顯示歡迎訊息
        self.refresh_yt_subtitle_mode_ui()
        self.root.update_idletasks()
        self.show_welcome_message()

    def _get_cookie_opts(self):
        """根據使用者選擇的瀏覽器，回傳 yt-dlp 的 cookie 參數列表。"""
        browser = self.cookie_browser_var.get()
        if browser == "none":
            return []
        return ["--cookies-from-browser", browser]

    def _get_ytdlp_js_runtime_opts(self):
        """自動偵測 yt-dlp 可用的 JS runtime，提升 YouTube 資訊與字幕擷取成功率。"""
        if self._yt_js_runtime_cache is not None:
            return list(self._yt_js_runtime_cache)

        runtime_candidates = [
            ("deno", shutil.which("deno")),
            ("node", shutil.which("node")),
            ("bun", shutil.which("bun")),
        ]

        quickjs_path = shutil.which("quickjs") or shutil.which("qjs")
        if quickjs_path:
            runtime_candidates.append(("quickjs", quickjs_path))

        for runtime_name, runtime_path in runtime_candidates:
            if runtime_path:
                self._yt_js_runtime_cache = ["--js-runtimes", f"{runtime_name}:{runtime_path}"]
                if not self._yt_js_runtime_notice_shown:
                    self.log(f"  ℹ️ 已啟用 yt-dlp JavaScript runtime：{runtime_name}")
                    self._yt_js_runtime_notice_shown = True
                return list(self._yt_js_runtime_cache)

        self._yt_js_runtime_cache = []
        if not self._yt_js_runtime_notice_shown:
            self.log("  ⚠️ 未偵測到 deno/node/bun/quickjs；YouTube 字幕或格式清單可能不完整。")
            self._yt_js_runtime_notice_shown = True
        return []

    def _get_ytdlp_command_base(self):
        """回傳可實際啟動 yt-dlp 的命令前綴，避免 embed Python 下 `-m yt_dlp` 匯入失敗。"""
        ytdlp_exe = self.py_dir / "Scripts" / "yt-dlp.exe"
        if ytdlp_exe.exists():
            return [str(ytdlp_exe)]

        ytdlp_main = self.lib_dir / "yt_dlp" / "__main__.py"
        if ytdlp_main.exists():
            return [str(self.local_python), str(ytdlp_main)]

        return [str(self.local_python), "-m", "yt_dlp"]

    def refresh_start_button_text(self):
        """依當前分頁與字幕模式更新主按鈕文字。"""
        current_tab = self.notebook.index("current")
        vfmt = self.video_format_var.get().upper() if hasattr(self, 'video_format_var') else "MKV"
        if current_tab == 0:
            self.start_btn.config(text=f"一鍵製作 {vfmt} 伴唱帶", bg="#E91E63")
        elif current_tab == 1:
            self.start_btn.config(text="立即下載 YouTube 檔案", bg="#2196F3")
        elif current_tab == 2:
            self.start_btn.config(text=f"製作本地影片 KTV ({vfmt})", bg="#9C27B0")
        else:
            self.start_btn.config(text="開始批量分離音檔", bg="#4CAF50")

    def refresh_yt_subtitle_mode_ui(self):
        """依分頁與勾選狀態顯示字幕模式列。"""
        current_tab = self.notebook.index("current") if hasattr(self, "notebook") else 0
        if current_tab == 0 and self.yt_cc_var.get():
            self.yt_subtitle_mode_row.pack(fill=tk.X, pady=2)
        else:
            self.yt_subtitle_mode_row.pack_forget()
        if hasattr(self, "start_btn"):
            self.refresh_start_button_text()

    @staticmethod
    def extract_youtube_video_id(url):
        """從常見 YouTube 網址格式中擷取影片 ID。"""
        match = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([0-9A-Za-z_-]{11})", url)
        return match.group(1) if match else None

    def normalize_subtitle_filename(self, subtitle_file, desired_stem):
        """將字幕檔改為固定主檔名，副檔名統一為 .srt。"""
        try:
            subtitle_path = Path(subtitle_file)
            if not subtitle_path.exists():
                return subtitle_file
            desired_path = subtitle_path.parent / f"{desired_stem}.srt"
            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)
            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass
            shutil.move(str(subtitle_path), str(desired_path))
            return str(desired_path)
        except Exception:
            return subtitle_file

    def _toggle_dl_separate_options(self):
        """根據音訊分離 Checkbox 顯示或隱藏分離設定區"""
        if self.dl_do_separate_var.get():
            self.dl_sep_options_frame.pack(fill=tk.X, pady=(2, 4), padx=0)
        else:
            self.dl_sep_options_frame.pack_forget()

    def on_tab_changed(self, event):
        """當分頁切換時，自動更新啟動按鈕文字，並顯示/隱藏對應的核心設定列"""
        current_tab = self.notebook.index("current")

        # Tab 1=YouTube KTV, Tab 2=純下載, Tab 3=本地KTV, Tab 4=批量分離
        is_download_only = (current_tab == 1)
        # 純下載 Tab 不需要 AI/KTV 相關設定，隱藏避免混淆
        rows_for_ai = [self.opt_row, self.format_row, self.ktv_row, self.track_row, self.mix_row]
        for row in rows_for_ai:
            if is_download_only:
                row.pack_forget()
            else:
                row.pack(fill=tk.X, pady=2)

        # 額外影片選項只在 KTV 影片流程顯示；YouTube CC 僅在第 1 籤頁顯示
        if current_tab in (0, 2):
            self.extra_video_row.pack(fill=tk.X, pady=2)
            self.force_1080p_chk.pack(side=tk.LEFT)
            self.yt_cc_chk.pack_forget()
            if current_tab == 0:
                self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))
            else:
                self.yt_cc_var.set(False)
        else:
            self.extra_video_row.pack_forget()
            if current_tab != 0:
                self.yt_cc_var.set(False)
        self.refresh_yt_subtitle_mode_ui()

    def update_vocal_mix_label(self):
        """更新導唱混合比例顯示文字。"""
        vocal_pct = int(round(float(self.vocal_mix_var.get())))
        inst_pct = max(0, 100 - vocal_pct)
        self.vocal_mix_label_var.set(f"人聲 {vocal_pct}% / 伴奏 {inst_pct}%")

    def on_start_click(self):
        """智能啟動按鈕：根據當前分頁決定執行對應功能"""
        current_tab = self.notebook.index("current")
        if current_tab == 0:
            self.start_yt_process()
        elif current_tab == 1:
            self.start_pure_download()
        elif current_tab == 2:
            self.start_local_v_process()
        else:
            self.start_separation()

    def browse_local_video(self):
        file_paths = filedialog.askopenfilenames(
            title="選擇影片檔案",
            filetypes=[("影片檔案", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm"), ("所有檔案", "*.*")]
        )
        if file_paths:
            for fp in file_paths:
                fp_abs = str(Path(fp).absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))

    def browse_local_v_folder(self):
        folder_path = filedialog.askdirectory(title="選擇影片資料夾")
        if folder_path:
            for fp in Path(folder_path).glob("*.mp4"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.mkv"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.avi"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.mov"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.wmv"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))
            for fp in Path(folder_path).glob("*.webm"):
                fp_abs = str(fp.absolute())
                if fp_abs not in self.v_list:
                    self.v_list.append(fp_abs)
                    self.v_listbox.insert(tk.END, os.path.basename(fp_abs))

    def remove_selected_v(self):
        selected = self.v_listbox.curselection()
        for index in reversed(selected):
            self.v_list.pop(index)
            self.v_listbox.delete(index)

    def clear_v_list(self):
        self.v_list =[]
        self.v_listbox.delete(0, tk.END)

    def start_local_v_process(self):
        if not self.v_list:
            messagebox.showwarning("警告", "請先加入影片檔案！")
            return
        if self.is_processing: return

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在進行批次影片處理...", "orange")
        
        threading.Thread(target=self.local_v_batch_process, daemon=True).start()

    def local_v_batch_process(self):
        try:
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir): os.makedirs(output_dir)
            
            total = len(self.v_list)
            for i, video_path in enumerate(self.v_list):
                if not self.is_processing or self.cancel_event.is_set():
                    self.log("🛑 批次處理已中止。")
                    break
                
                video_stem = Path(video_path).stem
                self.log(f"\n--- 正在處理 ({i+1}/{total}): {os.path.basename(video_path)} ---")
                
                # 更新 Listbox 顯示目前處理中（透過 root.after 確保在主執行緒執行）
                def _update_listbox(idx=i):
                    self.v_listbox.selection_clear(0, tk.END)
                    self.v_listbox.selection_set(idx)
                    self.v_listbox.see(idx)
                self.root.after(0, _update_listbox)
                
                temp_audio = Path(output_dir) / f"{video_stem}_temp_audio.mp3"
                
                # 1. 擷取音訊
                progress_base = int((i / total) * 100)
                progress_step = int(100 / total)
                
                self.update_progress(progress_base + int(progress_step * 0.1), f"正在擷取音訊 ({i+1}/{total})")
                self.log("  > 正在從影片擷取音訊...")
                ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
                extract_cmd =[
                    str(ffmpeg_exe), "-y", "-i", video_path,
                    "-vn", "-acodec", "libmp3lame", "-ab", "320k", str(temp_audio)
                ]
                subprocess.run(extract_cmd, check=True, creationflags=self.subp_flags)
                
                # 2. 執行分離
                self.update_progress(progress_base + int(progress_step * 0.3), f"正在 AI 分離 ({i+1}/{total})")
                success = self.run_audio_separator(str(temp_audio), output_dir)
                
                if success:
                    # 分隔完成，現在整理檔案
                    self.log("  > 正在整理產出檔案...")
                    voc_file, inst_file = self.consolidate_stems(str(temp_audio), video_path, output_dir)
                    
                    if voc_file and inst_file:
                        # 3. 合成 KTV 影片
                        vfmt = self.video_format_var.get()
                        self.update_progress(progress_base + int(progress_step * 0.8), f"正在合成 {vfmt.upper()} ({i+1}/{total})")
                        output_file = Path(output_dir) / f"{video_stem}_KTV.{vfmt}"
                        mkv_success = self.synthesize_mkv(video_path, voc_file, inst_file, str(output_file))
                        
                        if mkv_success:
                            self.log(f"✅ 成功生成 {vfmt.upper()}: {output_file.name}")
                        else:
                            self.log(f"❌ {video_stem} MKV 合成失敗。")
                    else:
                        self.log(f"❌ {video_stem} 找不到分離後的必要檔案。")
                else:
                    self.log(f"❌ {video_stem} 音訊分離失敗。")

            self.update_progress(100, "批次處理完成")
            self.log("\n✨ 所有影片批次處理任務已結束！")
            self.root.after(0, lambda: messagebox.showinfo("完成", f"已完成 {total} 個影片的處理！\n檔案已儲存至: {output_dir}"))
            if os.name == 'nt' and os.path.exists(output_dir):
                self.root.after(100, lambda: os.startfile(output_dir))
                
        except Exception as e:
            self.log(f"❌ 批次處理中出錯: {str(e)}")
        finally:
            self.finish_processing()

    def start_pure_download(self):
        """純下載邏輯：不進行 AI 分離與合成"""
        url = self.yt_dl_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "請輸入 YouTube 網址！")
            return
        if self.is_processing: return

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在下載 YouTube 檔案...", "orange")
        
        threading.Thread(target=self.pure_download_process, args=(url,), daemon=True).start()

    def pure_download_process(self, url):
        try:
            output_dir = self.output_dir_var.get()
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            dl_type = self.dl_type_var.get()
            quality  = self.dl_quality_var.get()
            do_separate = self.dl_do_separate_var.get()

            self.log(f"🚀 開始下載任務 (格式: {dl_type.upper()}，畫質: {quality}，音訊分離: {'是' if do_separate else '否'})...")

            # 預先提取 video_id，讓檔名包含唯一識別碼，避免同名影片撞名或誤拿舊檔
            video_id = self.extract_youtube_video_id(url) or None

            mp4_result: TaskResult | None = None
            mp3_result: TaskResult | None = None

            if dl_type in ["both", "mp4"]:
                self.log("  > 正在下載 MP4...")
                mp4_result = self.pure_download_file(url, output_dir, "mp4", quality, video_id=video_id)

            if dl_type in ["both", "mp3"]:
                self.log("  > 正在下載 MP3...")
                mp3_result = self.pure_download_file(url, output_dir, "mp3", quality, video_id=video_id)

            # 判斷各格式是否成功
            mp4_ok = (mp4_result.success if mp4_result else True) if dl_type in ["both", "mp4"] else True
            mp3_ok = (mp3_result.success if mp3_result else True) if dl_type in ["both", "mp3"] else True
            download_success = mp4_ok and mp3_ok

            # 若已取消，不顯示結果彈窗
            if self.cancel_event.is_set():
                self.log("\n🛑 任務已取消。")
                return

            # 若勾選音訊分離，對 MP3 進行 AI 分離
            if do_separate and dl_type in ["both", "mp3"]:
                self.log("\n🎵 開始 AI 音訊分離（人聲／伴奏）...")
                # 僅使用本次下載回傳的明確路徑，避免拿到輸出資料夾內的舊 MP3。
                target_mp3 = None
                if mp3_result and mp3_result.success and mp3_result.path:
                    target_mp3 = mp3_result.path

                if target_mp3:
                    self.log(f"  > 正在分離: {Path(target_mp3).name}")
                    original_model = self.model_var.get()
                    original_format = self.output_format_var.get()
                    self.model_var.set(self.dl_sep_model_var.get().split(" ")[0])
                    self.output_format_var.set(self.dl_sep_format_var.get())
                    sep_success = self.run_audio_separator(target_mp3, output_dir)
                    self.model_var.set(original_model)
                    self.output_format_var.set(original_format)
                    if sep_success:
                        self.log("  ✅ 音訊分離完成！")
                    else:
                        self.log("  ❌ 音訊分離失敗，請檢查日誌。")
                else:
                    self.log("  ⚠️ 找不到剛下載的 MP3 檔案，跳過音訊分離。")
            elif do_separate and dl_type == "mp4":
                self.log("\n⚠️ 音訊分離需要 MP3 檔案。請選擇「MP3 + MP4」或「僅 MP3」以啟用分離。")

            if download_success:
                self.log("\n✅ 所有任務已全部完成！")
                self.root.after(0, lambda: messagebox.showinfo(
                    "完成", "YouTube 下載" + ("及音訊分離" if do_separate else "") + "成功！"))
                if os.name == 'nt' and os.path.exists(output_dir):
                    self.root.after(100, lambda: os.startfile(output_dir))
            else:
                failed = []
                if not mp4_ok:
                    failed.append("MP4")
                if not mp3_ok:
                    failed.append("MP3")
                self.log(f"\n⚠️ 部分任務未完成：{', '.join(failed)} 下載失敗，請查看日誌。")
                self.root.after(0, lambda: messagebox.showwarning(
                    "部分失敗", f"以下項目下載失敗：{', '.join(failed)}\n請查看日誌。"))
        except Exception as e:
            self.log(f"❌ 下載過程中出錯: {str(e)}")
        finally:
            self.finish_processing()

    def pure_download_file(self, url, output_dir, file_type, quality="1080", video_id=None):
        """純下載單一格式，完全不走 KTV/分離邏輯，檔名直接使用影片標題。"""
        ytdlp_cmd_base = self._get_ytdlp_command_base()
        # 確保 yt_dlp 模組能從 lib_dir 找到（--target 安裝後不在 site-packages 中）
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)

        common_opts = [
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress"
        ] + self._get_cookie_opts()

        # 含 video_id 時使用唯一檔名，避免同名影片互相覆蓋或誤判「新增檔案」
        if video_id:
            out_template = os.path.join(output_dir, f"%(title).80s_{video_id}.%(ext)s")
        else:
            out_template = os.path.join(output_dir, "%(title).100s.%(ext)s")

        if file_type == "mp3":
            cmd = ytdlp_cmd_base + common_opts + [
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "320K",
                "-o", out_template,
                url
            ]
        else:
            # MP4 畫質映射
            if quality == "best":
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                fmt = (f"bestvideo[ext=mp4][height<={quality}]"
                       f"+bestaudio[ext=m4a]"
                       f"/best[ext=mp4][height<={quality}]"
                       f"/best[ext=mp4]/best")
            cmd = ytdlp_cmd_base + common_opts + [
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", out_template,
                url
            ]

        self.log(f"    執行: {file_type.upper()} 下載中...")
        # 記錄下載前的輸出目錄檔案清單，供事後驗證
        ext = "mp3" if file_type == "mp3" else "mp4"
        files_before = set(Path(output_dir).glob(f"*.{ext}"))

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True,
            creationflags=self.subp_flags, encoding='utf-8', errors='replace',
            env=ytdlp_env
        )
        self._current_process = process
        last_percent = -1

        for line in process.stdout:
            if self.cancel_event.is_set():
                process.terminate()
                self.log("🛑 下載已取消。")
                return TaskResult(success=False, error="cancelled")
            line = line.strip()
            if not line:
                continue
            # 進度條：只在整數 % 變化時才輸出，避免洗版
            if "[download]" in line and "%" in line:
                m = re.search(r"(\d+\.\d+)%", line)
                if m:
                    pct = float(m.group(1))
                    if int(pct) > last_percent:
                        self.log(f"    {line}")
                        last_percent = int(pct)
                        self.update_progress(int(pct), f"下載 {file_type.upper()}")
            else:
                # 顯示所有非進度條訊息（ffmpeg、警告、錯誤、Destination 等）
                prefix = "  ❌ " if "ERROR" in line.upper() else "    "
                self.log(f"{prefix}{line}")

        process.wait()
        self._current_process = None

        # 驗證是否真的產出了新檔案
        files_after = set(Path(output_dir).glob(f"*.{ext}"))
        new_files = files_after - files_before
        if new_files:
            newest = max(new_files, key=lambda f: f.stat().st_mtime)
            for nf in new_files:
                self.log(f"  ✅ {file_type.upper()} 下載完成：{nf.name}")
            return TaskResult(success=True, path=str(newest))
        elif process.returncode == 0:
            if video_id:
                matching_files = sorted(
                    Path(output_dir).glob(f"*_{video_id}.{ext}"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True
                )
                if matching_files:
                    self.log(f"  ✅ {file_type.upper()} 檔案已存在，直接使用：{matching_files[0].name}")
                    return TaskResult(success=True, path=str(matching_files[0]))
            self.log(f"  ⚠️ yt-dlp 回報成功但在輸出目錄找不到新的 {ext.upper()} 檔案。")
            self.log(f"     可能原因：ffmpeg 未安裝／路徑錯誤、格式合併失敗，請查看上方日誌。")
            return TaskResult(success=False, error="yt-dlp 回報成功但找不到輸出檔")
        else:
            self.log(f"  ❌ {file_type.upper()} 下載失敗（代碼: {process.returncode}）。")
            return TaskResult(success=False, error=f"exit code {process.returncode}")

    def quick_paste_url(self, event):
        """點擊輸入框時，若剪貼簿包含新的 YouTube 網址，則自動更新貼上"""
        try:
            clipboard = self.root.clipboard_get().strip()
            current_val = self.yt_url_var.get().strip()
            
            if clipboard and clipboard != current_val:
                # 簡單驗證是否為 YouTube 網址（涵蓋 shorts、嵌入、標準格式）
                if "youtube.com/" in clipboard or "youtu.be/" in clipboard:
                    self.yt_url_var.set(clipboard)
                    self.log(f"📋 已從剪貼簿更新網址: {clipboard}")
                    # 提示播放清單只下載第一支
                    if "list=" in clipboard and "watch?v=" not in clipboard and "/shorts/" not in clipboard:
                        self.log("⚠️ 偵測到播放清單連結，本工具僅會下載第一支影片（已加入 --no-playlist）。")
        except Exception:
            pass  # 剪貼簿為空或格式不支援

    def quick_paste_dl_url(self, event):
        """Tab 2 專屬：點擊輸入框時自動貼上剪貼簿中的 YouTube 網址"""
        try:
            clipboard = self.root.clipboard_get().strip()
            current_val = self.yt_dl_url_var.get().strip()
            if clipboard and clipboard != current_val:
                if "youtube.com/" in clipboard or "youtu.be/" in clipboard:
                    self.yt_dl_url_var.set(clipboard)
                    self.log(f"📋 [下載分頁] 已從剪貼簿貼上網址: {clipboard}")
                    if "list=" in clipboard and "watch?v=" not in clipboard and "/shorts/" not in clipboard:
                        self.log("⚠️ 偵測到播放清單，僅下載第一支影片（--no-playlist）。")
        except Exception:
            pass

    def show_welcome_message(self):
        welcome_text = (
            "==================================================\n"
            f" 🎵 歡迎使用 {APP_NAME}\n"
            f" {APP_DESCRIPTION}\n"
            "==================================================\n"
            "【快速入門】\n"
            "1. YouTube 轉 MKV：貼上網址，點擊「一鍵製作」即可自動完成。\n"
            "2. 本地分離：切換至分頁，加入 MP3 檔案，點擊「開始分離」。\n"
            "--------------------------------------------------\n"
            "💡 提示：點擊 YouTube 網址框可自動貼上剪貼簿內容。\n"
            "💡 建議：初次使用請確保環境已「初始化/修復」完成。\n"
            "==================================================\n"
            "🚀 系統就緒，請選擇功能分頁開始使用。\n"
        )
        self.log_area.insert(tk.END, welcome_text + "\n")
        self.log_area.see(tk.END)

    def log(self, message):
        self.root.after(0, lambda: self._safe_log(message))

    def _safe_log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)

    def update_progress(self, percent, text=None):
        self.root.after(0, lambda: self._safe_update_progress(percent, text))

    def _safe_update_progress(self, percent, text):
        self.progress_bar['value'] = percent
        if text: self.status_var.set(f"狀態: {text} ({percent}%)")
        else: self.progress_text.config(text=f"{percent}%")

    def browse_file(self):
        filenames = filedialog.askopenfilenames(filetypes=[("Audio files", "*.mp3 *.wav *.flac *.m4a"), ("All files", "*.*")])
        if filenames:
            for f in filenames:
                f_abs = str(Path(f).absolute())
                if f_abs not in self.file_list:
                    self.file_list.append(f_abs)
                    self.file_listbox.insert(tk.END, os.path.basename(f_abs))

    def remove_selected_file(self):
        selected = self.file_listbox.curselection()
        for index in reversed(selected):
            self.file_list.pop(index)
            self.file_listbox.delete(index)

    def clear_files(self):
        self.file_list =[]
        self.file_listbox.delete(0, tk.END)

    def browse_output_dir(self):
        directory = filedialog.askdirectory()
        if directory: self.output_dir_var.set(directory)

    def update_status(self, text, color="blue"):
        self.root.after(0, lambda: self._safe_update_status(text, color))

    def _safe_update_status(self, text, color):
        self.status_var.set(f"狀態: {text}")
        self.status_label.config(fg=color)

    def _get_target_ai_dir(self, install_mode="auto"):
        """根據安裝模式決定主要 AI 套件目錄。"""
        if install_mode == "cpu":
            return self.lib_dir
        if install_mode == "gpu":
            return self.gpu_lib_dir
        if install_mode == "both":
            return self.gpu_lib_dir if self._is_nvidia_gpu_present() else self.lib_dir
        return self.gpu_lib_dir if self._is_nvidia_gpu_present() else self.lib_dir

    def _get_runtime_ai_dir(self, device=None):
        target = device or self.device_var.get()
        return self.gpu_lib_dir if target == "gpu" else self.lib_dir

    def _has_onnxruntime_package(self, target_dir):
        """檢查指定 AI 套件目錄是否已有 onnxruntime 核心包（非僅 metadata）。"""
        try:
            # 必須檢查實際的套件資料夾與 __init__.py 是否存在，而非僅檢查 dist-info
            # 這能有效過濾掉安裝不完整或僅殘留 metadata 的情況
            pkg_dir = target_dir / "onnxruntime"
            return pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
        except Exception:
            return False

    def _build_python_env(self, lib_dir, include_gpu_runtime=False):
        """依據 CPU / GPU 模式建立隔離的 Python 執行環境。"""
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

        deduped = []
        seen = set()
        for p in search_paths:
            if p and p not in seen:
                deduped.append(p)
                seen.add(p)

        env["PATH"] = os.pathsep.join(deduped) + os.pathsep + env.get("PATH", "")
        return env

    def _probe_onnxruntime_stack(self, lib_dir, expect_gpu=False):
        """快速檢查指定套件目錄內的 ONNX Runtime 是否可正常使用。"""
        if not self.local_python.exists() or not lib_dir.exists():
            return "STACK_MISSING"

        env = self._build_python_env(lib_dir, include_gpu_runtime=expect_gpu)
        lib_dir_posix = str(lib_dir).replace("\\", "/")
        check_script = f"""
import sys, os
# 強制將目標目錄置於 sys.path 首位，並使用正斜線避免 Windows 轉義問題
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
        # 增加診斷資訊：若找不到模組，輸出當前 sys.path 的前幾個項目
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
                encoding="utf-8", errors="replace"
            )
            return (res.stdout or "").strip() or "ORT_NO_OUTPUT"
        except subprocess.TimeoutExpired:
            return "ORT_TIMEOUT"
        except Exception as e:
            return f"ORT_ERR:{str(e)}"

    def _ensure_runtime_stack_ready(self, device):
        """
        確保執行前的 AI 核心可用。
        - GPU 不可用時自動回退到 CPU
        - CPU 缺件或損壞時，自動嘗試補裝 CPU 核心並重試一次
        回傳: (is_ready, actual_device, runtime_lib_dir)
        """
        is_gpu = (device == "cuda")
        runtime_lib_dir = self._get_runtime_ai_dir("gpu" if is_gpu else "cpu")
        expected = "ORT_OK_GPU" if is_gpu else "ORT_OK_CPU"
        diag_out = self._probe_onnxruntime_stack(runtime_lib_dir, expect_gpu=is_gpu)
        self.log(f"🔍 運算環境診斷: {diag_out}")

        if diag_out == expected:
            return True, device, runtime_lib_dir

        if is_gpu:
            self.log("⚠️ GPU 核心尚未就緒，已切換至獨立 CPU 核心繼續執行。")
            self.root.after(0, lambda: self.device_var.set("cpu"))
            self._schedule_ort_fix_prompt(issue_key="gpu_runtime_fallback", delay_ms=3000)
            return self._ensure_runtime_stack_ready("cpu")

        repairable_tokens = ["ORT_ERR", "STACK_MISSING", "ORT_DLL_FAIL", "ORT_NO_OUTPUT", "ORT_TIMEOUT"]
        if any(token in diag_out for token in repairable_tokens):
            self.log("🛠️ 偵測到 CPU AI 核心缺失或損壞，正在自動補齊必要組件...")
            self.update_status("正在修復 CPU AI 核心...", "orange")

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

    def _schedule_ort_fix_prompt(self, issue_key="gpu_runtime_fallback", delay_ms=0):
        """統一排程修復提示，避免重複排入事件佇列。"""
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            self.log(f"ℹ️ [PROMPT] 已略過修復提示（本次已拒絕）: {issue_key}")
            return
        if self._ort_fix_prompt_active:
            self.log(f"ℹ️ [PROMPT] 修復提示顯示中，略過重複請求: {issue_key}")
            return
        if self._ort_fix_prompt_pending:
            self.log(f"ℹ️ [PROMPT] 修復提示已排程，略過重複請求: {issue_key}")
            return
        if issue_key in self._ort_fix_prompt_shown_keys:
            self.log(f"ℹ️ [PROMPT] 修復提示本次已顯示過，略過: {issue_key}")
            return

        self._ort_fix_prompt_pending = True
        self.log(f"ℹ️ [PROMPT] 已排程修復提示: {issue_key} ({delay_ms}ms)")

        def _fire():
            self._ort_fix_prompt_after_id = None
            self._ort_fix_prompt_pending = False
            self._prompt_ort_fix(issue_key=issue_key)

        self._ort_fix_prompt_after_id = self.root.after(delay_ms, _fire)

    def _reset_ort_fix_prompt_state(self, clear_history=False):
        """清理修復提示的排程與顯示狀態。"""
        if self._ort_fix_prompt_after_id is not None:
            try:
                self.root.after_cancel(self._ort_fix_prompt_after_id)
            except Exception:
                pass
            self._ort_fix_prompt_after_id = None

        self._ort_fix_prompt_pending = False
        self._ort_fix_prompt_active = False

        if clear_history:
            self._ort_fix_prompt_shown_keys.clear()
            self._ort_fix_prompt_suppressed_keys.clear()

        self.log(f"ℹ️ [PROMPT] 已重置修復提示狀態 clear_history={clear_history}")

    def check_gpu_env(self):
        self.log("\n---[開始 GPU 環境深度檢測] ---")

        # 先確認硬體層面是否有啟用的 NVIDIA GPU
        # 若無（例如筆電拔電源後切換至內顯），直接告知使用者，不進行後續修復流程
        if not self._is_nvidia_gpu_present():
            self.log("ℹ️ 系統目前未偵測到啟用的 NVIDIA 顯示卡。")
            self.log("💡 筆電使用者：請確認已插上電源，且系統已切換至獨立顯示卡（NVIDIA GPU）。")
            self.log("💡 若您的電腦沒有 NVIDIA 顯示卡，請使用 CPU 模式，這是正常狀態，無需修復。")
            messagebox.showinfo(
                "未偵測到 NVIDIA GPU",
                "目前系統未偵測到啟用的 NVIDIA 顯示卡。\n\n"
                "• 若您是筆電使用者，請插上電源後再試。\n"
                "• 若電腦沒有 NVIDIA 顯示卡，請直接使用 CPU 模式即可，不需要下載 GPU 組件。"
            )
            return

        if not self.local_python.exists():
            self.log("[ERROR] 內建 Python 核心尚未安裝，無法進行檢測。")
            if messagebox.askyesno("初始化環境", "偵測到環境尚未初始化，是否要現在開始下載並配置基礎環境？"):
                self.check_components(prompt=True)
            return

        gpu_lib_dir = self.gpu_lib_dir
        env = self._build_python_env(gpu_lib_dir, include_gpu_runtime=True)
        gpu_lib_dir_posix = str(gpu_lib_dir).replace("\\", "/")

        check_script = f"""
import sys, os
# 使用正斜線避免 Windows 轉義問題
target_lib = r'{gpu_lib_dir_posix}'
sys.path.insert(0, target_lib)

# 動態加入所有 NVIDIA 相關 DLL 目錄
if hasattr(os, 'add_dll_directory'):
    for root, dirs, files in os.walk(target_lib):
        if 'bin' in dirs or 'lib' in dirs:
            for d in ['bin', 'lib']:
                p = os.path.join(root, d)
                if os.path.isdir(p):
                    try: os.add_dll_directory(p)
                    except Exception: pass

libs_found =[]

try:
    import onnxruntime as ort
    libs_found.append('onnxruntime')
    print(f'[OK] ONNX Runtime 版本: {{ort.__version__}}')
    providers = ort.get_available_providers()
    print(f'[OK] 可用運算提供者 (Providers): {{providers}}')
    
    if 'CUDAExecutionProvider' in providers:
        print('[SUCCESS] ONNX CUDA 提供者已就緒')
    else:
        print('[INFO] ONNX 找不到 CUDA 提供者')
except ImportError:
    print(f'[ERROR] 尚未安裝 onnxruntime 套件 (搜尋路徑: {{target_lib}})')
except Exception as e:
    err_str = str(e)
    if 'DLL' in err_str or 'dll' in err_str or '初始化' in err_str or 'initialization routine' in err_str:
        print('[ERROR] onnxruntime DLL 載入失敗：安裝的是 GPU 版本但缺少 CUDA 環境')
        print('[HINT] 請點擊「一鍵修復/初始化環境」重新安裝正確版本')
    else:
        print(f'[ERROR] ONNX 檢測出錯: {{err_str}}')

try:
    import torch
    libs_found.append('torch')
    print(f'[OK] PyTorch 版本: {{torch.__version__}}')
    print(f'[DEBUG] PyTorch 路徑: {{torch.__file__}}')
    if torch.cuda.is_available():
        try:
            # 嘗試進行一個簡單的運算以確保算力相容
            test_tensor = torch.zeros(1).cuda()
            print(f'[OK] PyTorch CUDA 是否可用: True')
            print(f'[OK] 偵測到 GPU: {{torch.cuda.get_device_name(0)}}')
        except Exception as e:
            print(f'[ERROR] PyTorch 雖然偵測到 CUDA，但運算失敗 (可能是算力不相容): {{str(e)}}')
    else:
        if "+cpu" in torch.__version__:
            print('[INFO] 當前安裝的是 PyTorch CPU 版本，無法使用 GPU 加速')
        else:
            print('[INFO] PyTorch 偵測不到 CUDA，請檢查驅動程式')
except ImportError:
    print('[ERROR] 尚未安裝 torch 套件')
except Exception as e:
    print(f'[ERROR] PyTorch 檢測出錯: {{str(e)}}')

if not libs_found:
    print('[STATUS] 核心 AI 套件尚未安裝')
"""
        try:
            res = subprocess.run([str(self.local_python), "-c", check_script], 
                                 capture_output=True, text=True, env=env, 
                                 encoding='utf-8', errors='replace',
                                 creationflags=self.subp_flags)
            stdout_str = res.stdout.strip() if res.stdout else ""
            self.log(stdout_str)
            if res.stderr: self.log(f"[DEBUG] 錯誤資訊: {res.stderr.strip()}")
            
            # 檢測是否有安裝必要的 Python 套件
            libs_installed = "onnxruntime" in stdout_str and "torch" in stdout_str
            
            # 檢測 CUDA 是否可用 (必須 ONNX 和 PyTorch 兩者都就緒才算完全 ready)
            # 針對 RTX 50 系列 (sm_120)，如果 stderr 含有不相容警告，也視為未就緒
            is_sm120_incompatible = "sm_120 is not compatible" in res.stderr
            
            cuda_ready = ("ONNX CUDA 提供者已就緒" in stdout_str) and \
                         ("PyTorch CUDA 是否可用: True" in stdout_str) and \
                         (not is_sm120_incompatible)
            
            if not cuda_ready:
                if not libs_installed:
                    self.log("\n💡 偵測到核心組件缺失 (Torch 或 ONNX)。")
                    msg = ("偵測到程式尚未安裝「AI 加速組件」或組件損壞。\n\n"
                           "程式需要下載約 1.7GB 的加速庫才能發揮 GPU 效能。\n\n"
                           "是否立即執行「一鍵全自動修復」？")
                    if messagebox.askyesno("一鍵修復", msg):
                        self._start_async_setup()
                    return
                else:
                    # 如果套件已安裝但無法使用 CUDA
                    self.log("\n💡 偵測到 CUDA 加速環境配置不完全或不相容。")
                    if is_sm120_incompatible:
                        msg = ("偵測到您的 GPU (RTX 50 系列) 與當前 PyTorch 版本不相容。\n\n"
                               "程式需要重新下載支援 Blackwell 架構的運算核心 (CUDA 12.6+)。\n\n"
                               "是否立即執行「一鍵修復」？")
                    elif "PyTorch CUDA 是否可用: True" in stdout_str:
                        msg = ("您的 PyTorch 運作正常，但 ONNX 引擎尚未完全對接。\n\n"
                               "是否讓程式自動嘗試修復 DLL 補丁？")
                    else:
                        if "運算失敗" in stdout_str:
                            msg = ("偵測到您的 GPU 與當前 AI 組件版本不相容。\n\n"
                                   "這通常是因為您的顯示卡太新，需要更新版本的運算核心。\n\n"
                                   "是否立即執行「一鍵修復」以下載最新的相容版本？")
                        else:
                            msg = ("偵測到您的系統 PyTorch 無法使用 GPU (當前可能是 CPU 版本)。\n\n"
                                   "是否立即執行「一鍵修復」以下載正確的 GPU 版本？")
                    
                    if messagebox.askyesno("配置 CUDA 加速", msg):
                        self._start_async_setup()

        except Exception as e:
            self.log(f"[ERROR] 執行檢測失敗: {str(e)}")
        
        self.log("--- [檢測結束] ---\n")

    def _start_async_setup(self):
        if not self.is_processing:
            self.is_processing = True
            self.update_status("正在執行一鍵修復...", "orange")
            threading.Thread(target=self._async_setup_environment, daemon=True).start()

    def check_components(self, prompt=True):
        if self.is_processing: return

        # 偵測環境缺失
        has_gpu = self._is_nvidia_gpu_present()
        target_ai_dir = self._get_target_ai_dir("auto")
        cpu_ready = (self.lib_dir / "torch").exists() and (self.lib_dir / "audio_separator").exists() and self._has_onnxruntime_package(self.lib_dir)
        gpu_ready = (self.gpu_lib_dir / "torch").exists() and (self.gpu_lib_dir / "audio_separator").exists() and self._has_onnxruntime_package(self.gpu_lib_dir)
        missing = []
        if not self.local_python.exists(): missing.append("內建 Python 核心")
        if not (self.bin_dir / "ffmpeg.exe").exists(): missing.append("音訊引擎 FFmpeg")
        if not (target_ai_dir / "torch").exists(): missing.append("PyTorch 核心")
        if not (target_ai_dir / "audio_separator").exists(): missing.append("AI 音訊分離組件")
        if not self._has_onnxruntime_package(target_ai_dir): missing.append("ONNX Runtime 核心")

        # 啟動時自動檢查：
        # 有 GPU 時，只要 CPU 或 GPU 任一套 AI 核心可用即可啟動，避免只想用 CPU 的使用者被強迫修 GPU。
        startup_missing = []
        if not self.local_python.exists(): startup_missing.append("內建 Python 核心")
        if not (self.bin_dir / "ffmpeg.exe").exists(): startup_missing.append("音訊引擎 FFmpeg")
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

        # 如果完全沒有顯卡，不論是啟動還是手動，都只問是否安裝 CPU 版
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
            # 有顯卡的使用者，顯示完整選擇對話框
            dialog = tk.Toplevel(self.root)
            dialog.title("環境初始化 / 修復")
            dialog.geometry("450x320")
            dialog.transient(self.root)
            dialog.grab_set()
            
            dialog.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
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

            self.root.wait_window(dialog)
            
            if result["action"] != "confirm":
                if missing:
                    self.log("已取消環境初始化。")
                return
            install_mode = result["mode"]

        self.is_processing = True
        self.update_status("正在部署可攜式環境...", "orange")
        threading.Thread(target=self._async_setup_environment, args=(install_mode,), daemon=True).start()

    def _is_ytdlp_installed(self):
        """檢查 yt-dlp 是否已安裝（Scripts/ 或 lib_dir 均算）"""
        if (self.py_dir / "Scripts" / "yt-dlp.exe").exists():
            return True
        if (self.lib_dir / "yt_dlp" / "__main__.py").exists():
            return True
        return False

    def _check_ytdlp(self):
        """檢查 yt-dlp 是否安裝，若無則靜默安裝"""
        if not self._is_ytdlp_installed():
            self.log("🚀 偵測到缺少 YouTube 下載組件，正在自動補齊...")
            threading.Thread(target=self._install_ytdlp_silent, daemon=True).start()

    def _install_ytdlp_silent(self):
        try:
            result = subprocess.run(
                [str(self.local_python), "-m", "pip", "install", "--upgrade", "yt-dlp",
                 "--target", str(self.lib_dir), "--no-warn-script-location"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, creationflags=self.subp_flags
            )
            if result.returncode == 0:
                self.log("✅ YouTube 下載組件已補齊。")
            else:
                self.log(f"❌ YouTube 下載組件安裝失敗: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            self.log("❌ YouTube 下載組件安裝逾時（超過 120 秒），請手動點擊「初始化/修復環境」。")
        except Exception as e:
            self.log(f"❌ YouTube 下載組件安裝出錯: {str(e)}")

    def _async_setup_environment(self, install_mode="auto"):
        self.log(f"--- 開始自動化環境部署 (模式: {install_mode}) ---")

        # 1. 強化目錄建立邏輯
        # 逐層檢查並建立目錄，增加路徑存在性診斷
        setup_dirs = [
            ("音訊引擎", self.bin_dir),
            ("Python環境", self.py_dir),
            ("CPU AI函式庫", self.lib_dir),
            ("GPU AI函式庫", self.gpu_lib_dir),
            ("模型目錄", self.models_dir)
        ]

        for name, d in setup_dirs:
            try:
                # 確保父目錄 YouTube_KTV_Maker 本身存在
                if not d.parent.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)

                # 建立子目錄
                d.mkdir(parents=True, exist_ok=True)
                self.log(f"📂 目錄已就緒: {d.name}")
            except Exception as e:
                # 增加診斷資訊：顯示完整路徑以利排錯
                self.log(f"❌ 無法建立 {name} 目錄: {d}")
                self.log(f"   錯誤訊息: {str(e)}")
                # 如果連目錄都建不起來，後續下載必敗，直接中斷
                self.finish_processing()
                return

        # 2. 部署 Python 核心
        if not self.local_python.exists():
            self.log("🚀 正在下載內建 Python 核心 (約 10MB)...")
            # 確保 download_portable_python 內部不會再次觸發目錄找不到的錯誤
            if not self.download_portable_python():
                self.log("❌ Python 下載失敗，請檢查網路連線。")
                self.finish_processing()
                return
        else:
            self.fix_python_pth()

        # 再次確認 python.exe 是否真的存在於磁碟上
        if self.local_python.exists():
            self.log("✅ 內建 Python 核心已就緒。")
        else:
            self.log("❌ Python 部署異常：路徑存在但找不到執行檔。")
            self.finish_processing()
            return

        # 3. 部署 FFmpeg
        # 這裡改用 .is_file() 判斷更精確
        if not (self.bin_dir / "ffmpeg.exe").is_file():
            self.log("🚀 正在下載音訊引擎 FFmpeg (約 100MB+)...")
            if not self.download_ffmpeg():
                self.log("❌ FFmpeg 下載失敗。")
                # 雖然 FFmpeg 失敗，但若只是分離音檔可能還能運作，可考慮不 return
        else:
            self.log("✅ 音訊引擎已就緒。")

        # 4. 檢查組件 (audio-separator 與 GPU 支援)
        self.log("🔍 正在進行 AI 運算環境深度檢查...")
        packages_ok = False
        target_ai_dir = self._get_target_ai_dir(install_mode)
        expect_gpu_stack = (target_ai_dir == self.gpu_lib_dir)
        
        # 只要 torch 或 audio_separator 其中一個不在，就判定為不完整
        has_torch = (target_ai_dir / "torch").exists()
        has_sep = (target_ai_dir / "audio_separator").exists()
        has_ort = self._has_onnxruntime_package(target_ai_dir)
        
        if has_torch and has_sep and has_ort:
            try:
                # 測試是否能 import 核心組件並檢查是否有 GPU 支援
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

# 先偵測主機是否有 NVIDIA 顯示卡
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

# 先嘗試 import onnxruntime，單獨捕捉 DLL 失敗
try:
    import onnxruntime as ort
    ort_import_ok = True
except ImportError as e:
    ort_err = str(e)
except Exception as e:
    # onnxruntime-gpu 在無 CUDA 環境下 import 時 DLL 初始化失敗會走到這裡
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
        # 沒有顯卡且安裝的是 CPU 版本，這是正常狀態
        print('CHECK_RESULT:CPU_OK')
    elif not has_nvidia_gpu and not is_cpu_build:
        # 沒有顯卡但裝了 GPU 版本，標記為需修復
        print('CHECK_RESULT:WRONG_BUILD_FOR_CPU')
    else:
        print(f'CHECK_RESULT:NO_CUDA providers={{providers}} cuda={{cuda_available}}')
"""
                env = self._build_python_env(target_ai_dir, include_gpu_runtime=expect_gpu_stack)
                res = subprocess.run([str(self.local_python), "-c", check_cmd],
                                     capture_output=True, text=True, creationflags=self.subp_flags,
                                     env=env, timeout=60,
                                     encoding="utf-8", errors="replace")
                
                check_out = res.stdout.strip() if res.stdout else ""
                self.log(f"  > 核心組件狀態: {check_out}")
                
                if "CHECK_RESULT:OK" in check_out:
                    packages_ok = True
                elif "CHECK_RESULT:CPU_OK" in check_out:
                    self.log("✅ 無 NVIDIA 顯示卡，CPU 版本組件運作正常。")
                    packages_ok = True
                elif "CHECK_RESULT:SM120_INCOMPATIBLE" in check_out:
                    self.log("🔍 偵測到 RTX 50 系列顯示卡與現有運算核心不相容，將執行強制升級。")
                    packages_ok = False
                elif "CHECK_RESULT:WRONG_BUILD_FOR_CPU" in check_out:
                    self.log("🔍 偵測到安裝的是 GPU 版本但主機無 NVIDIA 顯示卡，將重裝為 CPU 版本。")
                    packages_ok = False
                else:
                    self.log("🔍 偵測到加速組件不完整或不支援 GPU，將執行修復。")
                    packages_ok = False
            except Exception as e:
                self.log(f"⚠️ 檢查過程發生異常: {str(e)}")
        else:
            if not has_torch: self.log("🔍 偵測到缺少 PyTorch 核心組件。")
            if not has_sep: self.log("🔍 偵測到缺少音訊分離核心組件。")
            if not has_ort: self.log("🔍 偵測到缺少 ONNX Runtime 核心。")
            packages_ok = False
  
        if not packages_ok or install_mode != "auto":
            self.log(f"🚀 準備執行 AI 運算組件安裝/修復 (模式: {install_mode})...")
            if not self.install_packages_locally(install_mode=install_mode):
                self.log("❌ AI 組件安裝失敗，請查看上方詳細日誌。")
                self.finish_processing()
                return
            self.log("✅ AI 組件安裝/修復完成。")
            # 安裝完成後自動執行一次環境檢測並切換 UI
            self.log("🔄 正在根據新環境自動切換運算裝置...")
            self._startup_ort_check()
        else:
            self.log("✅ AI 運算組件已就緒。")
        
        # 4. 檢查 yt-dlp
        if not self._is_ytdlp_installed():
            self.log("🚀 正在補齊 YouTube 下載組件 (yt-dlp)...")
            try:
                result = subprocess.run(
                    [str(self.local_python), "-m", "pip", "install", "--upgrade", "yt-dlp",
                     "--target", str(self.lib_dir), "--no-warn-script-location"],
                    creationflags=self.subp_flags, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=180
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
        
        self.update_progress(100, "全部就緒")
        self._reset_ort_fix_prompt_state(clear_history=True)
        self._startup_component_prompt_shown = False
        self.log("--- 環境部署完成 ---")
        self.finish_processing()

    def fix_python_pth(self):
        try:
            pth_files = list(self.py_dir.glob("*._pth"))
            if not pth_files:
                self.log("⚠️ 找不到 Python .pth 設定檔，跳過路徑校正。")
                return
            pth_file = pth_files[0]
            with open(pth_file, "r") as f:
                lines = f.readlines()
            
            # 清理與規範化 lines
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
            
            # 動態偵測 Python zip 名稱（相容 3.10/3.11/3.12 等版本）
            py_zip = next((f.name for f in self.py_dir.glob("python*.zip")), "python310.zip")
            
            # 僅保留 Python 自身必要路徑，CPU/GPU 套件路徑改由執行時動態注入，
            # 避免 CPU / GPU 兩套 onnxruntime 互相污染。
            required = [
                py_zip, 
                ".", 
                "Lib/site-packages", 
                "import site"
            ]
            needs_update = removed_legacy
            
            for item in required:
                if item not in lines:
                    # 檢查是否被註解了
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

    def download_portable_python(self):
        # 多個備用載點，依序嘗試
        py_urls = [
            # 主要：Python 官方 FTP
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            # 備用 1：GitHub mirror (python-build-standalone)
            "https://github.com/indygreg/python-build-standalone/releases/download/20230826/cpython-3.10.13+20230826-x86_64-pc-windows-msvc-shared-pgo-full.tar.zst",
            # 備用 2：官方 FTP 另一版本
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            # 備用 3：官方 FTP 3.11
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
        ]
        # 只使用標準 embed zip 格式的載點（tar.zst 格式不同，移除）
        py_urls = [
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.10.9/python-3.10.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
            "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip",
        ]

        # ── 步驟 1：確保目標目錄存在並可寫入 ──────────────────────────────────
        # urlretrieve 本身不會建立父目錄，若目錄不存在會噴 [Errno 2]；
        # 此外 Windows 長路徑限制或權限問題也會讓 mkdir 靜默失敗，
        # 所以這裡分三步：建立 → 驗證存在 → 實際寫入測試。
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

        # 實際寫入測試，確認磁碟空間與權限正常
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
        # 清除上次可能殘留的不完整檔案
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

                # 驗證 zip 完整性
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

    def _download_reporthook(self, count, block_size, total_size):
        if total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            percent = min(percent, 100)
            self.update_progress(percent, "正在下載")
            if percent % 10 == 0:
                self._last_log_percent = getattr(self, '_last_log_percent', -1)
                if percent != self._last_log_percent:
                    self.log(f"  > 下載進度: {percent}%")
                    self._last_log_percent = percent

    def download_ffmpeg(self):
        # 主要來源：BtbN GitHub Release；備用來源：gyan.dev essentials
        primary_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
        fallback_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        zip_path = self.bin_dir / "ffmpeg.zip"

        # 確保目錄存在且可寫入
        try:
            self.bin_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log(f"❌ 無法建立 FFmpeg 目錄: {self.bin_dir}\n   原因: {str(e)}")
            return False
        _test = self.bin_dir / ".write_test"
        try:
            _test.write_text("ok"); _test.unlink()
        except Exception as e:
            self.log(f"❌ FFmpeg 目錄無寫入權限: {self.bin_dir}\n   原因: {str(e)}")
            return False

        for attempt, url in enumerate([primary_url, fallback_url], 1):
            try:
                self.log(f"🚀 正在連線至下載伺服器 (來源 {attempt}/2)...")
                # 建立不驗證 SSL 的 context，解決 CERTIFICATE_VERIFY_FAILED 問題
                ssl_context = ssl._create_unverified_context()
                
                self._last_log_percent = -1
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))
                opener.addheaders = [('User-agent', 'Mozilla/5.0')]
                urllib.request.install_opener(opener)
                
                urllib.request.urlretrieve(url, str(zip_path), reporthook=self._download_reporthook)
                self.log("📦 正在提取 FFmpeg 引擎與共享函式庫 (DLLs)...")
                with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                    for file in zip_ref.namelist():
                        # 統一將路徑改為正斜線進行判斷
                        normalized_file = file.replace('\\', '/')
                        # 提取 bin 目錄下的所有 exe 和 dll
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
                    try: os.remove(str(zip_path))
                    except Exception: pass
                if attempt < 2:
                    self.log("🔄 嘗試備用下載來源...")
        
        self.log("❌ FFmpeg 所有下載來源均失敗，請檢查網路連線。")
        return False

    def _clean_ai_packages_in_dir(self, target_dir):
        patterns = [
            "torch*", "torchvision*", "torchaudio*",
            "onnxruntime*", "onnxruntime_gpu*",
            "audio_separator*",
            "nvidia*"
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

    def _install_ai_stack(self, target_dir, target_mode="cpu", is_rtx50=False):
        target_dir.mkdir(parents=True, exist_ok=True)

        if target_mode == "cpu":
            self.log("📦 正在部署獨立 CPU AI 核心...")
            torch_index = "https://download.pytorch.org/whl/cpu"
            torch_ver = "2.5.1+cpu"
            tv_ver = "0.20.1+cpu"
            ta_ver = "2.5.1+cpu"
            install_steps = [
                ["setuptools", "wheel", "pip"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime", "audio-separator"]
            ]
        else:
            if is_rtx50:
                self.log("📦 正在部署獨立 GPU AI 核心（cu128 / RTX 50）...")
                torch_index = "https://download.pytorch.org/whl/cu128"
                torch_ver = "2.7.1+cu128"
                tv_ver = "0.22.1+cu128"
                ta_ver = "2.7.1+cu128"
            else:
                self.log("📦 正在部署獨立 GPU AI 核心（cu124）...")
                torch_index = "https://download.pytorch.org/whl/cu124"
                torch_ver = "2.5.1+cu124"
                tv_ver = "0.20.1+cu124"
                ta_ver = "2.5.1+cu124"
            install_steps = [
                ["setuptools", "wheel", "pip"],
                ["nvidia-cuda-runtime-cu12", "nvidia-cudnn-cu12", "nvidia-cublas-cu12",
                 "nvidia-curand-cu12", "nvidia-cufft-cu12", "nvidia-cuda-nvrtc-cu12", "nvidia-ml-py"],
                ["--extra-index-url", torch_index,
                 f"torch=={torch_ver}", f"torchvision=={tv_ver}", f"torchaudio=={ta_ver}",
                 "onnxruntime-gpu", "audio-separator[gpu]"]
            ]

        self._clean_ai_packages_in_dir(target_dir)

        pip_base_cmd = [
            str(self.local_python), "-m", "pip", "install",
            "--target", str(target_dir),
            "--upgrade",
            "--retries", "10",
            "--timeout", "100",
            "--no-warn-script-location"
        ]
        pip_env = self._build_python_env(target_dir, include_gpu_runtime=(target_mode == "gpu"))

        for i, step_pkgs in enumerate(install_steps):
            self.log(f"📦 正在執行安裝進度 ({i+1}/{len(install_steps)}): {' '.join(step_pkgs[-3:])}...")
            cmd = pip_base_cmd + step_pkgs
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, creationflags=self.subp_flags, encoding='utf-8',
                errors='replace', env=pip_env
            )

            has_output = False
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
                            self.update_status(f"正在下載組件 ({i+1}/{len(install_steps)})...", "orange")

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

    def install_packages_locally(self, install_mode="auto"):
        try:
            self.log("📥 下載 Pip 安裝工具...")
            pip_script = self.py_dir / "get-pip.py"
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", pip_script)

            self.log("📥 正在安裝 Pip 組件...")
            subprocess.run([str(self.local_python), str(pip_script)], creationflags=self.subp_flags, check=True)

            pip_check = subprocess.run(
                [str(self.local_python), "-m", "pip", "--version"],
                capture_output=True, text=True, creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
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
                        encoding="utf-8", errors="replace"
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
            err_text = str(e)
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                self.log("❌ 磁碟空間不足，無法繼續安裝 AI 組件。")
                self.log("💡 建議先釋放磁碟空間後再重試。")
                self.log("💡 若不需要 GPU 加速，請改選「僅安裝 CPU 版」，所需空間會比雙支援版少很多。")
            else:
                self.log(f"安裝錯誤: {err_text}")
            return False

    def _is_nvidia_gpu_present(self):
        """
        透過 wmic 實際查詢系統是否有 NVIDIA GPU（已啟用）。
        只查硬體，不依賴 CUDA/PyTorch，避免「GPU 停用但 CUDA driver 仍在」的誤判。
        注意：wmic 輸出的 Name 與 Status 是不同欄位（不在同一行），
              只需確認 Name 欄位中有 NVIDIA 字樣即可，停用的裝置不會出現在列表中。
        備援方案：若 wmic 失敗（部分 Windows 11 已移除），改用 PowerShell。
        """
        # 方法一：wmic（Windows 10 / 部分 Windows 11）
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "Name"],
                capture_output=True, text=True, timeout=10,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
            )
            if result.returncode == 0:
                output = result.stdout.upper()
                if "NVIDIA" in output:
                    return True
                # wmic 成功執行但沒有 NVIDIA -> 確定沒有
                return False
        except Exception:
            pass  # wmic 不存在時繼續嘗試備援

        # 方法二：PowerShell（Windows 11 wmic 已移除時的備援）
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Display | Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=15,
                creationflags=self.subp_flags,
                encoding="utf-8", errors="replace"
            )
            if "NVIDIA" in result.stdout.upper():
                return True
        except Exception:
            pass

        return False

    def _startup_ort_check(self):
        """
        啟動時背景執行緒：
        1. 先檢查 CPU 核心是否可用
        2. 若主機有 NVIDIA GPU，再額外檢查 GPU 核心
        CPU / GPU 兩條路徑完全分開，避免 CPU 使用者被 GPU 套件拖垮。
        """
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
                self.root.after(0, lambda: self.device_var.set("gpu"))
                self._reset_ort_fix_prompt_state(clear_history=False)
            elif cpu_out == "ORT_OK_CPU":
                self.log("✅ 基礎環境已就緒（CPU 模式）。")
                if self.device_var.get() == "gpu":
                    self.root.after(0, lambda: self.device_var.set("cpu"))
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

    def _prompt_ort_fix(self, issue_key="gpu_runtime_fallback"):
        """彈窗詢問使用者是否立即修復 onnxruntime 版本問題"""
        if issue_key in self._ort_fix_prompt_suppressed_keys:
            self.log(f"ℹ️ [PROMPT] 已抑制修復提示，不再顯示: {issue_key}")
            return
        if self._ort_fix_prompt_active:
            self.log(f"ℹ️ [PROMPT] 修復提示已在顯示中: {issue_key}")
            return
        if self.is_processing:
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
                "是否立即自動修復？"
            )
            if answer:
                self._start_async_setup()
            else:
                self._ort_fix_prompt_suppressed_keys.add(issue_key)
                self.log(f"ℹ️ [PROMPT] 使用者已拒絕本次修復提示: {issue_key}")
        finally:
            self._ort_fix_prompt_active = False

    def _quick_check_gpu(self):
        """快速檢測 GPU 是否可用（不彈窗）"""
        if not self.local_python.exists(): return False

        # 先確認系統確實有啟用的 NVIDIA GPU，否則直接回傳 False
        # 這樣可避免筆電拔掉電源、GPU 被停用時，CUDA driver 仍存在而誤判為可用
        if not self._is_nvidia_gpu_present():
            return False
        if not (self.gpu_lib_dir / "torch").exists():
            return False
        return self._probe_onnxruntime_stack(self.gpu_lib_dir, expect_gpu=True) == "ORT_OK_GPU"

    def start_separation(self):
        if not self.file_list:
            messagebox.showwarning("警告", "請先加入音檔！")
            return
        if self.is_processing: return

        # 如果選用 GPU 但環境尚未檢測或不完全，先提示檢測
        if self.device_var.get() == "gpu":
            self.log("🚀 啟動前檢查 GPU 環境...")
            # 這裡不彈出視窗，直接執行背景檢測
            if not self._quick_check_gpu():
                if messagebox.askyesno("環境未就緒", "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？\n(若不修復將改用 CPU 運行，速度較慢)"):
                    self.check_gpu_env()
                    return
                else:
                    self.log("⚠️ 使用者選擇忽略，將嘗試改用 CPU 模式。")
                    self.device_var.set("cpu")

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在處理中...", "orange")
        
        # 啟動批次處理線程
        threading.Thread(target=self.batch_process, daemon=True).start()

    def start_yt_process(self):
        url = self.yt_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "請輸入 YouTube 網址！")
            return
        if self.is_processing: return

        # 如果選用 GPU 但環境尚未檢測或不完全，先提示檢測
        if self.device_var.get() == "gpu":
            if not self._quick_check_gpu():
                if messagebox.askyesno("環境未就緒", "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？"):
                    self.check_gpu_env()
                    return
                else:
                    self.device_var.set("cpu")

        self.is_processing = True
        self.cancel_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.update_status("正在從 YouTube 下載並處理...", "orange")
        
        threading.Thread(target=self.yt_process, args=(url,), daemon=True).start()

    def yt_process(self, url):
        try:
            output_dir = self.output_dir_var.get()
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            self._last_downloaded_subtitle = None
            subtitle_mode = self.yt_subtitle_mode_var.get() if self.yt_cc_var.get() else "none"

            self.log(f"--- 正在處理 YouTube 影片: {url} ---")
            self.update_progress(10, "正在獲取影片資訊")
            if subtitle_mode == "srt_only":
                self.log("📝 字幕模式：只抓 SRT，不封裝進成品")
            elif subtitle_mode == "mux":
                self.log("📝 字幕模式：抓字幕並合成到成品")

            # 1. 僅下載 MP4 (影像+音訊)，節省頻寬
            video_file = self.download_youtube(
                url,
                output_dir,
                mode="mp4",
                download_subtitles=(subtitle_mode in ("srt_only", "mux"))
            )

            if not video_file:
                self.log("❌ YouTube 影片下載失敗。")
                return

            # 2. 從 MP4 中擷取 MP3 音訊進行分離，避免二次下載
            self.update_progress(30, "正在從影片擷取音訊")
            self.log("  > 正在從下載的影片中擷取音訊...")
            video_path = Path(video_file)
            audio_file = str(video_path.parent / f"{video_path.stem}_audio.mp3")

            ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
            extract_cmd = [
                str(ffmpeg_exe), "-y", "-i", video_file,
                "-vn", "-acodec", "libmp3lame", "-ab", "320k", audio_file
            ]

            try:
                subprocess.run(extract_cmd, check=True, creationflags=self.subp_flags)
                self.log(f"  ✅ 音訊擷取完成: {os.path.basename(audio_file)}")
            except Exception as e:
                self.log(f"  ❌ 音訊擷取失敗: {str(e)}")
                return

            self.update_progress(40, "正在分離人聲與伴奏")

            # 3. 執行分離
            success = self.run_audio_separator(audio_file, output_dir)

            if success:
                # 分隔完成，現在整理檔案
                self.log("📦 正在整理並重新命名產出檔案...")
                voc_file, inst_file = self.consolidate_stems(audio_file, video_file, output_dir)

                if voc_file and inst_file:
                    # 4. 合成 KTV 影片
                    vfmt = self.video_format_var.get()
                    self.update_progress(80, f"正在合成 {vfmt.upper()} 伴唱帶")
                    output_file = Path(output_dir) / f"{Path(video_file).stem}_KTV.{vfmt}"
                    subtitle_for_mux = self._last_downloaded_subtitle if subtitle_mode == "mux" else None
                    mkv_success = self.synthesize_mkv(
                        video_file,
                        voc_file,
                        inst_file,
                        str(output_file),
                        subtitle_file=subtitle_for_mux
                    )

                    if mkv_success:
                        self.log(f"✅ 成功生成 {vfmt.upper()} 伴唱帶: {output_file.name}")
                        if self._last_downloaded_subtitle:
                            self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, str(output_file))
                        self.update_progress(100, "處理完成")
                        self.root.after(0, lambda: messagebox.showinfo("成功", f"YouTube 處理完成！\n檔案已儲存至: {output_dir}"))
                        if os.name == 'nt' and os.path.exists(output_dir):
                            self.root.after(100, lambda: os.startfile(output_dir))
                    else:
                        self.log("❌ MKV 合成失敗。")
                else:
                    self.log("❌ 找不到分離後的必要檔案 (人聲或伴奏)。")
            else:
                self.log("❌ 音訊分離失敗。")

        except Exception as e:
            self.log(f"❌ 處理過程中發生未預期錯誤: {str(e)}")
        finally:
            self.finish_processing()

    def consolidate_stems(self, input_audio, reference_video, output_dir):
        """整理分離後的音軌：重新命名人聲，並合併多音軌為伴奏 (針對 Demucs)"""
        fmt = self.output_format_var.get()
        audio_stem = Path(input_audio).stem
        video_stem = Path(reference_video).stem
        out_path = Path(output_dir)
        
        def safe_stem(stem):
            """清除非法字元並截短，避免 Windows 路徑過長"""
            return VocalForgeStudioApp.sanitize_filename(stem, max_len=60)

        voc_final = out_path / f"{safe_stem(video_stem)}_vocals.{fmt}"
        inst_final = out_path / f"{safe_stem(video_stem)}_instrumental.{fmt}"
        
        # 1. 尋找人聲
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and "(Vocals)" in f.name and f.suffix == f".{fmt}":
                if voc_final.exists(): os.remove(voc_final)
                f.rename(voc_final)
                break
        
        # 2. 尋找伴奏 (MDX 模式)
        found_inst = False
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem) and any(x in f.name for x in["(Instrumental)", "(No Vocals)"]) and f.suffix == f".{fmt}":
                if inst_final.exists(): os.remove(inst_final)
                f.rename(inst_final)
                found_inst = True
                break
        
        # 3. 如果沒找到伴奏，檢查是否為 Demucs 多音軌模式
        if not found_inst:
            stems_to_merge =[]
            # Demucs 標籤通常包含這些；6s 模型還會多拆出 Guitar / Piano
            tags = [
                "(Bass)", "(Drums)", "(Other)", "(Guitar)", "(Piano)",
                "_Bass", "_Drums", "_Other", "_Guitar", "_Piano"
            ]
            for f in out_path.iterdir():
                if f.name.startswith(audio_stem) and any(tag in f.name for tag in tags) and f.suffix == f".{fmt}":
                    stems_to_merge.append(f)
            
            if stems_to_merge:
                stem_names = ", ".join(sorted(f.name for f in stems_to_merge))
                self.log(f"  > 偵測到 Demucs 多音軌，正在合併 {len(stems_to_merge)} 個音軌為伴奏...")
                self.log(f"  > 參與合併的音軌: {stem_names}")
                inputs = []
                for f in stems_to_merge:
                    inputs.extend(["-i", str(f)])
                
                filter_str = "".join([f"[{i}:a]" for i in range(len(stems_to_merge))])
                filter_str += f"amix=inputs={len(stems_to_merge)}:duration=first[out]"
                
                merge_cmd = [str(self.bin_dir / "ffmpeg.exe"), "-y"] + inputs + \
                           ["-filter_complex", filter_str, "-map", "[out]", "-b:a", "320k", str(inst_final)]
                
                try:
                    subprocess.run(merge_cmd, check=True, creationflags=self.subp_flags)
                    found_inst = True
                except Exception as e:
                    self.log(f"  ❌ 合併音軌失敗: {str(e)}")

        # 4. 清理所有相關暫存檔
        self.log("🧹 正在清理暫存檔案...")
        for f in out_path.iterdir():
            if f.name.startswith(audio_stem):
                try: f.unlink()
                except Exception as e:
                    self.log(f"  ⚠️ 清理暫存檔失敗: {f.name} ({str(e)})")
        if Path(input_audio).exists():
            try: os.remove(input_audio)
            except Exception as e:
                self.log(f"  ⚠️ 清理原始音檔失敗: {str(e)}")
            
        return (str(voc_final) if voc_final.exists() else None, 
                str(inst_final) if inst_final.exists() else None)
        # 注意：不在此處呼叫 finish_processing()，由呼叫端負責

    def finish_processing(self):
        self.is_processing = False
        self.runner.is_running = False
        self.runner.current_process = None
        self._current_process = None
        self.cancel_event.clear()
        self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.cancel_btn.config(state=tk.DISABLED))
        self.update_status("準備就緒", "green")

    def cancel_processing(self):
        """中止當前正在執行的任務"""
        if not self.is_processing:
            return
        self.runner.cancel()

    @staticmethod
    def sanitize_filename(title, max_len=80):
        """將標題截短並清除 Windows 非法字元，避免路徑過長導致各種失敗"""
        # 移除 Windows 路徑非法字元
        illegal = r'\/:*?"<>|'
        for ch in illegal:
            title = title.replace(ch, '_')
        
        # 移除控制字元與不可見字元，避免檔名出現亂碼感
        title = "".join(char for char in title if char.isprintable())
        
        # 合併連續空白/底線
        title = re.sub(r'[\s_]+', '_', title).strip('_')
        
        # 截短：優先考慮字元數，但也要注意 Windows 的位元組限制
        if len(title) > max_len:
            title = title[:max_len]
        
        # 確保 UTF-8 編碼後的長度不會過長 (Windows 單個檔名限制約 255 bytes)
        while len(title.encode('utf-8', errors='replace')) > 180:
            title = title[:-1]
            
        return title.strip() or 'video'

    def download_youtube(self, url, output_dir, mode="both", download_subtitles=False):
        """使用 yt-dlp 下載影片與音訊"""
        self.log("🚀 正在下載 YouTube 內容...")
        self._last_downloaded_subtitle = None
        
        # 提取影片 ID 作為可靠的檔案追蹤標記（涵蓋標準、Shorts、嵌入、youtu.be 格式）
        video_id = self.extract_youtube_video_id(url) or "temp_id"

        ytdlp_cmd_base = self._get_ytdlp_command_base()
        # 確保 yt_dlp 模組能從 lib_dir 找到
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)

        common_opts =[
            "--no-playlist",
            "--ffmpeg-location", str(self.bin_dir),
            "--encoding", "utf-8",
            "--progress"
        ] + self._get_ytdlp_js_runtime_opts() + self._get_cookie_opts()

        def run_ytdlp_with_logging(cmd, step_name):
            self.log(f"  > 正在下載 {step_name}...")
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True, creationflags=self.subp_flags, 
                encoding='utf-8', errors='replace', env=ytdlp_env
            )
            self._current_process = process
            
            last_percent = -1
            recent_errors = []
            recent_lines = []
            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    return False, ["使用者取消"]
                line = line.strip()
                if not line: continue
                recent_lines.append(line)
                if len(recent_lines) > 12:
                    recent_lines.pop(0)
                
                # 顯示進度資訊
                if "[download]" in line and "%" in line:
                    match = re.search(r"(\d+\.\d+)%", line)
                    if match:
                        percent = float(match.group(1))
                        if int(percent) > last_percent:
                            self.log(f"    {line}")
                            last_percent = int(percent)
                elif any(x in line for x in ["[ffmpeg]", "Merging", "Extracting", "Destination"]):
                    self.log(f"    {line}")
                elif "ERROR" in line.upper():
                    self.log(f"  ❌ {line}")
                    recent_errors.append(line)
                    if len(recent_errors) > 6:
                        recent_errors.pop(0)
            
            process.wait()
            self._current_process = None
            if process.returncode != 0 and recent_errors:
                self.log(f"  ⚠️ {step_name} 失敗摘要：{recent_errors[-1][:220]}")
            elif process.returncode != 0 and recent_lines:
                self.log(f"  ⚠️ {step_name} 最後輸出：{recent_lines[-1][:220]}")
            return process.returncode == 0, (recent_errors or recent_lines)

        def find_downloaded_file(pattern):
            """使用 glob 尋找包含特定 ID 的檔案，解決 Windows 編碼導致的路徑變數亂碼問題"""
            files = list(Path(output_dir).glob(pattern))
            if files:
                # 根據修改時間排序，取最新的
                files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                return str(files[0])
            return None

        video_file = None
        audio_file = None

        # --- 取得安全短檔名（強制使用 UTF-8 編碼解決 Windows 亂碼問題）---
        safe_name = video_id  # fallback
        try:
            # 增加 PYTHONIOENCODING 確保 yt-dlp 輸出為 UTF-8，並使用 --print 取得純標題
            ytdlp_env_info = ytdlp_env.copy()
            ytdlp_env_info["PYTHONIOENCODING"] = "utf-8"
            title_result = subprocess.run(
                ytdlp_cmd_base + ["--no-playlist"] + self._get_ytdlp_js_runtime_opts() + ["--print", "%(title)s", url],
                capture_output=True, text=True, creationflags=self.subp_flags,
                timeout=30, encoding='utf-8', errors='replace', env=ytdlp_env_info
            )
            raw_title = title_result.stdout.strip().splitlines()[0] if title_result.stdout.strip() else ""
            if raw_title:
                # 額外清理：移除標題中可能導致檔名解析問題的 [ 或 ]
                raw_title = raw_title.replace('[', '(').replace(']', ')')
                safe_name = self.sanitize_filename(raw_title, max_len=80)
                self.log(f"  📝 影片標題: {raw_title}")
                self.log(f"  📝 安全檔名: {safe_name}")
        except Exception as e:
            self.log(f"  ⚠️ 取得標題失敗，使用影片 ID 作為檔名: {str(e)}")

        if download_subtitles and mode in ["both", "mp4"]:
            self._last_downloaded_subtitle = self.download_youtube_subtitle(url, output_dir, video_id)

        # 下載 MP4
        if mode in ["both", "mp4"]:
            mp4_out = os.path.join(output_dir, f"{safe_name}_{video_id}.mp4")
            video_format = "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best"

            mp4_attempts = [
                (
                    "MP4 影片",
                    ytdlp_cmd_base + common_opts + [
                        "-f", video_format,
                        "--merge-output-format", "mp4",
                        "-o", mp4_out,
                        url
                    ]
                ),
                (
                    "MP4 相容模式",
                    ytdlp_cmd_base + common_opts + [
                        "-f", "bv*+ba/b",
                        "--recode-video", "mp4",
                        "-o", mp4_out,
                        url
                    ]
                )
            ]

            mp4_ok = False
            last_mp4_errors = []
            for idx, (attempt_name, mp4_cmd) in enumerate(mp4_attempts, start=1):
                if idx > 1:
                    self.log("  ℹ️ 主要 MP4 格式失敗，改用相容模式重試...")
                success, err_lines = run_ytdlp_with_logging(mp4_cmd, attempt_name)
                last_mp4_errors = err_lines
                if not success:
                    continue

                if os.path.exists(mp4_out):
                    video_file = mp4_out
                    mp4_ok = True
                    self.log(f"  ✅ MP4 下載完成: {os.path.basename(video_file)}")
                    if self._last_downloaded_subtitle:
                        self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, video_file)
                    break
                else:
                    video_file = find_downloaded_file("*.mp4")
                    if video_file:
                        mp4_ok = True
                        self.log(f"  ✅ MP4 下載完成: {os.path.basename(video_file)}")
                        if self._last_downloaded_subtitle:
                            self._last_downloaded_subtitle = self.align_subtitle_filename(self._last_downloaded_subtitle, video_file)
                        break

            if not mp4_ok:
                if last_mp4_errors:
                    self.log(f"  ❌ MP4 下載失敗摘要：{last_mp4_errors[-1][:220]}")
                self.log("  ❌ MP4 下載過程出錯")
                if mode == "both": return None, None
                else: return None
            elif not video_file:
                self.log("  ❌ MP4 下載失敗: 找不到下載後的檔案")
                if mode == "both": return None, None
                else: return None

        # 下載 MP3
        if mode in ["both", "mp3"]:
            self.log("  > 正在準備 MP3 音訊...")
            mp3_out = os.path.join(output_dir, f"{safe_name}_{video_id}_audio.mp3")
            
            mp3_cmd = ytdlp_cmd_base + common_opts + [
                "-x", "--audio-format", "mp3",
                "--audio-quality", "320K",
                "-o", mp3_out,
                url
            ]
            
            mp3_ok, _mp3_errs = run_ytdlp_with_logging(mp3_cmd, "MP3 音訊")
            if mp3_ok:
                if os.path.exists(mp3_out):
                    audio_file = mp3_out
                    self.log(f"  ✅ MP3 下載完成: {os.path.basename(audio_file)}")
                else:
                    audio_file = find_downloaded_file("*.mp3")
                    if audio_file:
                        self.log(f"  ✅ MP3 下載完成: {os.path.basename(audio_file)}")
                    else:
                        self.log("  ❌ MP3 下載失敗: 找不到下載後的檔案")
                        if mode == "both": return video_file, None
                        else: return None
            else:
                self.log("  ❌ MP3 下載過程出錯")
                if mode == "both": return video_file, None
                else: return None

        if mode == "both":
            return video_file, audio_file
        else:
            return video_file if mode == "mp4" else audio_file

    def download_youtube_subtitle(self, url, output_dir, video_id):
        """下載 YouTube 字幕，若同時存在多語字幕則優先取中文，其次英文。"""
        self.log("  > 正在檢查 YouTube CC 字幕...")

        ytdlp_cmd_base = self._get_ytdlp_command_base()

        # 使用 PYTHONIOENCODING 確保 yt-dlp 內部處理與輸出均為 UTF-8
        ytdlp_env = os.environ.copy()
        ytdlp_env["PYTHONPATH"] = str(self.lib_dir)
        ytdlp_env["PYTHONIOENCODING"] = "utf-8"
        js_runtime_opts = self._get_ytdlp_js_runtime_opts()
        cookie_opts = self._get_cookie_opts()

        subtitle_out = os.path.join(output_dir, f"{video_id}.%(ext)s")
        subtitle_patterns = [
            f"{video_id}*.srt",
            f"{video_id}*.vtt",
            f"{video_id}*.ass",
            f"{video_id}*.srv3",
        ]
        def clear_old_subtitles():
            for pattern in subtitle_patterns:
                for old_file in Path(output_dir).glob(pattern):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass

        def collect_subtitle_candidates():
            subtitle_candidates = []
            for pattern in subtitle_patterns:
                subtitle_candidates.extend(Path(output_dir).glob(pattern))
            return sorted(
                {str(path): path for path in subtitle_candidates}.values(),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

        def get_preferred_langs_from_metadata():
            preferred_groups = [
                ["zh-TW", "zh-Hant", "zh-HK", "cmn-Hant"],
                ["zh-CN", "zh-Hans", "cmn-Hans"],
                ["zh"],
                ["en"],
                ["ja-orig", "ja"],
            ]

            metadata_cmd = ytdlp_cmd_base + [
                "--skip-download",
                "--no-playlist",
                "--dump-single-json",
            ] + js_runtime_opts + cookie_opts + [url]

            try:
                result = subprocess.run(
                    metadata_cmd,
                    capture_output=True,
                    text=True,
                    creationflags=self.subp_flags,
                    timeout=60,
                    encoding='utf-8',
                    errors='replace',
                    env=ytdlp_env
                )
                if result.returncode != 0 or not result.stdout.strip():
                    err_preview = (result.stderr or result.stdout or "").strip()
                    if err_preview:
                        self.log(f"  ⚠️ 讀取字幕語言清單失敗（代碼 {result.returncode}）：{err_preview[:180]}")
                    return []

                data = json.loads(result.stdout)
                manual = {k for k in (data.get("subtitles") or {}).keys() if k and k != "live_chat"}
                auto = {k for k in (data.get("automatic_captions") or {}).keys() if k and k != "live_chat"}
                ordered = []

                def append_lang(lang):
                    if lang and lang not in ordered:
                        ordered.append(lang)

                def find_exact_or_prefix(available, target):
                    lower_map = {code.lower(): code for code in available}
                    exact = lower_map.get(target.lower())
                    if exact:
                        return exact
                    for code in available:
                        lower_code = code.lower()
                        lower_target = target.lower()
                        if lower_code.startswith(lower_target + "-") or lower_code.startswith(lower_target + "_"):
                            return code
                    return None

                for available_set in (manual, auto):
                    for group in preferred_groups:
                        for lang in group:
                            found = find_exact_or_prefix(available_set, lang)
                            if found:
                                append_lang(found)
                                break

                original_lang = data.get("language")
                if original_lang:
                    append_lang(original_lang)

                for lang in sorted(manual):
                    append_lang(lang)
                for lang in sorted(auto):
                    append_lang(lang)

                return ordered[:8]
            except Exception as e:
                self.log(f"  ⚠️ 讀取字幕語言清單失敗，改用精簡策略重試：{str(e)}")
                return []

        candidate_langs = get_preferred_langs_from_metadata()
        if candidate_langs:
            self.log(f"  ℹ️ 已挑選優先字幕語言：{', '.join(candidate_langs[:5])}")
        else:
            candidate_langs = ["zh-TW", "zh-Hant", "zh-HK", "zh-CN", "zh-Hans", "en", "ja-orig", "ja"]
            self.log("  ℹ️ 無法讀取完整字幕清單，改用精簡語言順序嘗試下載。")

        last_return_code = 0
        for lang in candidate_langs:
            clear_old_subtitles()
            self.log(f"  > 嘗試下載字幕語言：{lang}")

            cmd = ytdlp_cmd_base + [
                "--skip-download",
                "--no-playlist",
                "--ffmpeg-location", str(self.bin_dir),
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs", lang,
                "--sub-format", "srt/best",
                "--convert-subs", "srt",
                "-o", subtitle_out,
            ] + js_runtime_opts + cookie_opts + [
                url
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
                creationflags=self.subp_flags, encoding='utf-8', errors='replace',
                env=ytdlp_env
            )
            self._current_process = process

            for line in process.stdout:
                if self.cancel_event.is_set():
                    process.terminate()
                    self.log("🛑 字幕下載已取消。")
                    self._current_process = None
                    return None
                line = line.strip()
                if not line:
                    continue
                if any(token in line for token in ["subtitle", "Subtitles", "Writing video subtitles", "Deleting original file"]):
                    self.log(f"    {line}")
                elif "WARNING" in line.upper():
                    self.log(f"  ⚠️ {line}")
                elif "ERROR" in line.upper():
                    self.log(f"  ❌ {line}")

            process.wait()
            self._current_process = None
            last_return_code = process.returncode
            subtitle_candidates = collect_subtitle_candidates()

            if subtitle_candidates:
                if process.returncode != 0:
                    self.log("  ⚠️ 字幕下載程序部分失敗，但已找到可用字幕檔，將直接採用。")
                selected = subtitle_candidates[0]
                self.log(f"  ✅ 已找到字幕檔：{selected.name}")
                return str(selected)

            if process.returncode == 0:
                self.log(f"  ℹ️ 語言 {lang} 沒有可下載字幕，改試下一個語言。")
            else:
                self.log(f"  ⚠️ 語言 {lang} 下載失敗，改試下一個語言。")

        if last_return_code != 0:
            self.log("  ⚠️ 字幕下載程序未成功完成，將略過字幕。")
            return None

        subtitle_candidates = collect_subtitle_candidates()
        if not subtitle_candidates:
            self.log("  ℹ️ 這支影片沒有可用的 YouTube CC 字幕。")
            return None

        prefer_keywords = [
            "zh-tw", "zh-hant", "zh-hk", "zh-cn", "zh-hans", ".zh", "_zh", "-zh",
            "cmn-hant", "cmn-hans", "en", "english", "ja-orig", "ja"
        ]
        selected = None
        lower_map = [(path, path.name.lower()) for path in subtitle_candidates]
        for keyword in prefer_keywords:
            for path, lower_name in lower_map:
                if keyword in lower_name:
                    selected = path
                    break
            if selected:
                break
        if not selected:
            selected = subtitle_candidates[0]

        self.log(f"  ✅ 已找到字幕檔：{selected.name}")
        return str(selected)

    def align_subtitle_filename(self, subtitle_file, target_media_file):
        """將字幕檔改名成與目標媒體檔完全同主檔名，副檔名固定為 .srt。"""
        try:
            subtitle_path = Path(subtitle_file)
            target_path = Path(target_media_file)
            if not subtitle_path.exists() or not target_path.exists():
                return subtitle_file

            # 統一改成和最終媒體檔同名，只保留 .srt
            desired_path = target_path.parent / f"{target_path.stem}.srt"

            if subtitle_path.resolve() == desired_path.resolve():
                return str(subtitle_path)

            if desired_path.exists():
                try:
                    desired_path.unlink()
                except Exception:
                    pass

            # 使用 shutil.move 替代 rename，跨磁碟機移動較穩健
            import shutil
            shutil.move(str(subtitle_path), str(desired_path))
            self.log(f"  📝 字幕檔已對齊命名：{desired_path.name}")
            return str(desired_path)
        except Exception as e:
            self.log(f"  ⚠️ 字幕檔重新命名失敗，保留原檔名：{str(e)}")
            return subtitle_file

    def synthesize_mkv(self, video_file, vocal_file, instrumental_file, output_file, subtitle_file=None):
        """合成 KTV 伴唱帶：支援雙音軌模式與左伴唱/右人聲單音軌模式"""
        vfmt = self.video_format_var.get().upper()
        track_mode = self.audio_track_mode_var.get()  # "dual" or "lr"
        vocal_mix = max(0.0, min(1.0, float(self.vocal_mix_var.get()) / 100.0))
        instrumental_mix = max(0.0, 1.0 - vocal_mix)
        vocal_pct = int(round(vocal_mix * 100))
        inst_pct = int(round(instrumental_mix * 100))
        force_1080p = self.force_1080p_var.get()
        
        self.log(f"🎬 正在合成 {vfmt} 伴唱帶（音軌模式：{'雙音軌' if track_mode == 'dual' else '左伴唱/右人聲'}）...")
        if track_mode == "dual":
            self.log(f"🎚️ 導唱混合比例：人聲 {vocal_pct}% / 伴奏 {inst_pct}%")
        else:
            self.log("🎚️ 目前為左伴唱／右人聲模式，混合比例設定不套用於此模式。")
        if force_1080p:
            self.log("🖼️ 已啟用強制等比輸出 1080p，必要時會補黑邊。")
        if subtitle_file and os.path.exists(subtitle_file):
            self.log(f"💬 將自動封裝 YouTube CC 字幕：{os.path.basename(subtitle_file)}")
        else:
            subtitle_file = None
        
        # 檢查輸入檔案是否存在
        missing = []
        if not os.path.exists(video_file): missing.append(f"影片檔: {os.path.basename(video_file)}")
        if not os.path.exists(vocal_file): missing.append(f"人聲檔: {os.path.basename(vocal_file)}")
        if not os.path.exists(instrumental_file): missing.append(f"伴奏檔: {os.path.basename(instrumental_file)}")
        
        if missing:
            self.log(f"  ❌ 合成失敗，缺少必要檔案：\n    - " + "\n    - ".join(missing))
            return False

        ffmpeg_exe = self.bin_dir / "ffmpeg.exe"
        cmd = [str(ffmpeg_exe), "-y", "-i", str(video_file)]

        if track_mode == "lr":
            # 左聲道=伴奏，右聲道=人聲，合成為單音軌立體聲
            # amerge 將兩個單聲道混成立體聲，pan 指定 L=伴奏 R=人聲
            cmd += [
                "-i", str(instrumental_file),    # 1: 伴奏 -> 左聲道
                "-i", str(vocal_file),           # 2: 人聲 -> 右聲道
            ]
            audio_filter = (
                "[1:a]pan=mono|c0=c0[inst_mono];"
                "[2:a]pan=mono|c0=c0[voc_mono];"
                "[inst_mono][voc_mono]amerge=inputs=2[lr]"
            )
            audio_maps = [
                "-map", "0:v",
                "-map", "[lr]",
                "-metadata:s:a:0", "title=左伴唱／右人聲",
                "-ac", "2",
            ]
        else:
            # 預設：雙音軌 (音軌1=伴唱+人聲混合, 音軌2=純伴奏)
            cmd += [
                "-i", str(vocal_file),           # 1: 人聲
                "-i", str(instrumental_file),    # 2: 伴奏
            ]
            audio_filter = f"[1:a][2:a]amix=inputs=2:duration=first:weights='{vocal_mix:.2f} {instrumental_mix:.2f}'[mix]"
            audio_maps = [
                "-map", "0:v",
                "-map", "[mix]",                 # 音軌 1: 導唱 (人聲+伴奏)
                "-map", "2:a",                   # 音軌 2: 純伴奏
                "-metadata:s:a:0", f"title=導唱 (人聲{vocal_pct}% + 伴奏{inst_pct}%)",
                "-metadata:s:a:1", "title=伴唱 (純伴奏)",
            ]

        subtitle_input_index = 3
        if subtitle_file:
            cmd += ["-i", str(subtitle_file)]

        cmd += [
            "-filter_complex", audio_filter,
            *audio_maps,
            "-c:a", "aac",
            "-b:a", "320k",
        ]

        if subtitle_file:
            cmd += [
                "-map", f"{subtitle_input_index}:0",
                "-disposition:s:0", "default",
                "-metadata:s:s:0", "title=YouTube CC"
            ]
            if vfmt == "MP4":
                cmd += ["-c:s", "mov_text"]
            else:
                cmd += ["-c:s", "srt"]

        if force_1080p:
            cmd += [
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                       "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "18",
                "-pix_fmt", "yuv420p"
            ]
        else:
            cmd += ["-c:v", "copy"]

        if vfmt == "MP4":
            cmd += ["-movflags", "+faststart"]

        cmd += [str(output_file)]
        
        try:
            subprocess.run(cmd, check=True, creationflags=self.subp_flags)
            self.log(f"  ✅ {vfmt} 合成完成: {os.path.basename(output_file)}")
            return True
        except Exception as e:
            self.log(f"  ❌ {vfmt} 合成出錯: {str(e)}")
            return False

    def batch_process(self):
        total = len(self.file_list)
        output_dir = self.output_dir_var.get()
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            for i, input_file in enumerate(self.file_list):
                if self.cancel_event.is_set():
                    self.log("🛑 批次分離已中止。")
                    break
                if not os.path.exists(input_file):
                    self.log(f"⚠️ 找不到檔案: {input_file}")
                    continue

                self.log(f"--- 正在處理 ({i+1}/{total}): {os.path.basename(input_file)} ---")
                self.update_progress(int(i / total * 100), f"正在處理 {i+1}/{total}")

                success = self.run_audio_separator(input_file, output_dir)

                if success:
                    self.log(f"✅ 檔案處理完成: {os.path.basename(input_file)}")
                else:
                    self.log(f"❌ 檔案處理失敗: {os.path.basename(input_file)}，請檢查上方日誌。")

            if not self.cancel_event.is_set():
                self.update_progress(100, "全部完成")
                self.root.after(0, lambda: messagebox.showinfo("成功", f"批次處理完成！\n已處理 {total} 個檔案。"))
                if os.name == 'nt' and os.path.exists(output_dir):
                    self.root.after(100, lambda: os.startfile(output_dir))
        except Exception as e:
            self.log(f"❌ 批次分離過程出錯: {str(e)}")
        finally:
            self.finish_processing()

    def run_audio_separator(self, input_file, output_dir):
        self.fix_python_pth()
        
        fmt = self.output_format_var.get()
        device = "cuda" if self.device_var.get() == "gpu" else "cpu"
        runtime_ready, device, runtime_lib_dir = self._ensure_runtime_stack_ready(device)
        if not runtime_ready:
            return False

        env = self._build_python_env(runtime_lib_dir, include_gpu_runtime=(device == "cuda"))
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
        
        # 取得選取的模型名稱
        selected_model = self.model_var.get().split(" ")[0]
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
                "--normalization", "0.9"
            ]

            if is_demucs:
                # Demucs 專用參數（htdemucs 系列使用 --demucs_ 前綴參數）
                command.extend([
                    "--demucs_segment_size", "None",
                    "--demucs_shifts", "2",
                    "--demucs_overlap", "0.25",
                ])
            else:
                # MDX 專用參數
                command.extend([
                    "--mdx_overlap", str(self.overlap_var.get()),
                    "--mdx_segment_size", "256",
                    "--mdx_hop_length", "1024"
                ])
                if self.denoise_var.get():
                    command.append("--mdx_enable_denoise")

            # 根據裝置選擇
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

            # 注意：不能用 glob，因為檔名含 [ ] 等字元在 glob 語法中是特殊字元，
            # 會導致匹配失敗。改用 os.listdir 直接字串比對。
            keywords = [f"_(Vocals)", f"_(Instrumental)", f"_(No Vocals)",
                        f"_(Bass)", f"_(Drums)", f"_(Other)"]
            try:
                for fname in os.listdir(str(out_path)):
                    if fname.startswith(input_stem) and fname.endswith(f".{fmt}"):
                        if any(kw in fname for kw in keywords):
                            return True
            except Exception:
                pass
            return False

        def run_model(model_name, is_retry=False):
            command, is_demucs = build_command(model_name)

            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding='utf-8', errors='replace',
                    creationflags=self.subp_flags, env=env
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

                        # 嘗試解析進度 (audio-separator 輸出通常包含百分比)
                        if "%" in line:
                            try:
                                match = re.search(r"(\d+)%", line)
                                if match:
                                    p = int(match.group(1))
                                    # 這裡的進度是單個檔案的，我們不更新全域進度條
                            except Exception:
                                pass

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

        success, reason = run_model(selected_model, is_retry=False)
        if success:
            return True

        if reason == "retry_with_demucs":
            self.log(f"🎯 回退模型: {selected_model} → {fallback_model}")
            retry_success, retry_reason = run_model(fallback_model, is_retry=True)
            if retry_success:
                self.log(f"✅ 已改用 `{fallback_model}` 完成音訊分離。")
                self.log("💡 若想恢復使用原本的 MDX 模型，請刪除舊的 .onnx 後重新下載。")
                return True

            if retry_reason == "unsupported_model":
                self.log("❌ 備援模型也無法載入，請執行「一鍵修復/初始化環境」，或手動清理模型目錄後再試。")
            else:
                self.log("❌ 已嘗試自動切換備援模型，但仍未成功完成分離。")

        return False

if __name__ == "__main__":
    root = tk.Tk()
    app = VocalForgeStudioApp(root)
    root.mainloop()

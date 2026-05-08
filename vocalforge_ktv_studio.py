import logging
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import customtkinter as ctk
import subprocess
from logging.handlers import RotatingFileHandler
from pathlib import Path
import multiprocessing
from services.task_result import TaskResult
from services.task_runner import TaskRunner
from services.ffmpeg_service import FfmpegService
from services.download_service import DownloadService
from services.separation_service import SeparationService
from services.environment_service import EnvironmentService

APP_NAME = "VocalForge KTV Studio"
APP_VERSION = "2.11.0"
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
        self.root.geometry("1040x1060")
        
        # 取得執行路徑 (EXE 所在目錄)
        # 注意：PyInstaller onefile 模式下 sys.executable 指向 EXE 本身（正確）
        # 但部分版本 sys._MEIPASS 指向暫存解壓目錄，必須用 sys.executable 而非 __file__
        if getattr(sys, 'frozen', False):
            exe_path = Path(sys.executable)
            # 若 EXE 被放在 Temp 目錄（代表是 onefile 解壓中間狀態），改用環境變數
            if 'Temp' in str(exe_path) or 'temp' in str(exe_path):
                # 嘗試從 _MEIPASS 的父目錄推算（onefile 解壓時 sys.executable 仍正確）
                # 這種情況實際上不應發生，但作為保護
                alt = os.environ.get('_MEIPASS2', '') or os.environ.get('PYINSTALLER_ORIG_EXEC', '')
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
                print(f"[warn] 無法建立目錄 {d.name}: {e}")
        
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
        self._current_process = None

        # 初始化檔案 log（在 Services 前，讓 debug_log 可用）
        self._file_logger = self._setup_file_logger()

        # --- Services ---
        self.ffmpeg_svc = FfmpegService(
            self.bin_dir / "ffmpeg.exe", self.log, self.subp_flags,
            debug_log_fn=self.debug_log,
        )
        self.dl_svc = DownloadService(
            py_dir=self.py_dir,
            lib_dir=self.lib_dir,
            bin_dir=self.bin_dir,
            log_fn=self.log,
            progress_fn=self.update_progress,
            cancel_event=self.cancel_event,
            subp_flags=self.subp_flags,
            cookie_browser_fn=lambda: self.cookie_browser_var.get() if hasattr(self, "cookie_browser_var") else "none",
            debug_log_fn=self.debug_log,
        )
        self.env_svc = EnvironmentService(
            app_dir=self.app_dir,
            bin_dir=self.bin_dir,
            py_dir=self.py_dir,
            lib_dir=self.lib_dir,
            gpu_lib_dir=self.gpu_lib_dir,
            models_dir=self.models_dir,
            local_python=self.local_python,
            subp_flags=self.subp_flags,
            log_fn=self.log,
            progress_fn=self.update_progress,
            status_fn=self.update_status,
            root=self.root,
            get_device_fn=lambda: self.device_var.get() if hasattr(self, "device_var") else "cpu",
            set_device_fn=lambda v: self.device_var.set(v) if hasattr(self, "device_var") else None,
            get_is_processing_fn=lambda: self.is_processing,
            start_setup_fn=self._start_async_setup,
        )
        self.sep_svc = SeparationService(
            local_python=self.local_python,
            models_dir=self.models_dir,
            log_fn=self.log,
            cancel_event=self.cancel_event,
            subp_flags=self.subp_flags,
            env_service=self.env_svc,
            ffmpeg_svc=self.ffmpeg_svc,
        )

        self.yt_url_var = tk.StringVar()
        self.setup_ui()

        # 啟動時自動檢查
        self.root.after(500, lambda: self.env_svc.check_components(prompt=False))

    def _setup_theme(self):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.ui = {
            "bg": "#F2F2F7",
            "surface": "#FFFFFF",
            "surface_soft": "#F9F9FB",
            "border": "#D1D1D6",
            "text": "#1C1C1E",
            "muted": "#6E6E73",
            "hint": "#8E8E93",
            "ink": "#1C1C1E",
            "ink_soft": "#E5F1FF",
            "primary": "#007AFF",
            "primary_hover": "#0066D6",
            "success": "#34C759",
            "success_hover": "#28A044",
            "danger": "#FF3B30",
            "danger_hover": "#D32F27",
            "warning": "#FF9500",
            "warning_hover": "#D67E00",
            "purple": "#AF52DE",
            "purple_hover": "#8F44B4",
            "pink": "#FF2D55",
            "pink_hover": "#D9163B",
            "console": "#F9F9FB",
            "console_text": "#1C1C1E",
        }
        self.fonts = {
            # ── 标题层 ─────────────────────────────────────────
            "title":     ("Segoe UI", 20, "bold"),   # App 主标题
            # ── 区块层 ─────────────────────────────────────────
            "section":   ("Segoe UI", 12, "bold"),   # 卡片 / 区块标题
            # ── 内容层 ─────────────────────────────────────────
            "body":      ("Segoe UI", 10),            # 表单标签、说明文字
            "body_bold": ("Segoe UI", 10, "bold"),    # 强调标签、状态文字
            "button":    ("Segoe UI", 10, "bold"),    # 所有按钮文字
            # ── 提示层 ─────────────────────────────────────────
            "small":     ("Segoe UI", 9),             # 辅助提示、进度、Badge
            # ── 等宽层 ─────────────────────────────────────────
            "mono":      ("Consolas", 10),            # 日志控制台
        }
        self.root.configure(bg=self.ui["bg"])
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = min(1040, screen_w)
        win_h = min(900, screen_h - 80)   # 留出工作列空间
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(800, 600)
        self.root.maxsize(screen_w, screen_h - 40)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=self.ui["surface"], borderwidth=0, tabmargins=(0, 0, 0, 10))
        style.configure(
            "TNotebook.Tab",
            background=self.ui["surface_soft"],
            foreground=self.ui["muted"],
            padding=(16, 9),
            font=self.fonts["body_bold"],
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.ui["primary"]), ("active", "#E5F1FF")],
            foreground=[("selected", "#FFFFFF"), ("active", self.ui["text"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=self.ui["surface_soft"],
            background=self.ui["primary"],
            bordercolor=self.ui["border"],
            lightcolor=self.ui["primary"],
            darkcolor=self.ui["primary"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=self.ui["surface"],
            background=self.ui["surface"],
            foreground=self.ui["text"],
            arrowcolor=self.ui["muted"],
            bordercolor=self.ui["border"],
            padding=(6, 4),
        )

    def _style_button(self, button, normal_bg=None, hover_bg=None, fg="white", primary=False):
        normal_bg = normal_bg or self.ui["primary"]
        hover_bg = hover_bg or self.ui["primary_hover"]
        font = self.fonts["button"]
        if isinstance(button, ctk.CTkButton):
            button.configure(
                fg_color=normal_bg,
                hover_color=hover_bg,
                text_color=fg,
                font=font,
                corner_radius=14,
                height=34,
            )
        else:
            button.configure(
                bg=normal_bg,
                fg=fg,
                activebackground=hover_bg,
                activeforeground=fg,
                disabledforeground="#A0A8B5",
                relief=tk.FLAT,
                bd=0,
                highlightthickness=0,
                cursor="hand2",
                font=font,
                padx=12,
                pady=7,
            )
        button._vf_normal_bg = normal_bg
        button._vf_hover_bg = hover_bg

        if not isinstance(button, ctk.CTkButton) and not getattr(button, "_vf_hover_bound", False):
            def _enter(_event, widget=button):
                if str(widget.cget("state")) != tk.DISABLED:
                    widget.configure(bg=getattr(widget, "_vf_hover_bg", hover_bg))

            def _leave(_event, widget=button):
                widget.configure(bg=getattr(widget, "_vf_normal_bg", normal_bg))

            button.bind("<Enter>", _enter, add="+")
            button.bind("<Leave>", _leave, add="+")
            button._vf_hover_bound = True

    def _set_action_button(self, text, normal_bg, hover_bg):
        self.start_btn.configure(text=text)
        self._style_button(self.start_btn, normal_bg, hover_bg, primary=True)

    def _toggle_settings_panel(self, force=None):
        if force is None:
            next_state = not self.settings_expanded.get()
        else:
            next_state = bool(force)
        self.settings_expanded.set(next_state)
        if next_state:
            self.settings_dropdown.pack(fill=tk.X, pady=(8, 0))
            self.settings_toggle_btn.configure(text="收合")
        else:
            self.settings_dropdown.pack_forget()
            self.settings_toggle_btn.configure(text="展開")

    def _handle_ctk_tab_changed(self):
        tab_name = self.tabview.get()
        self.current_tab_index = self.tab_names.index(tab_name)
        self.on_tab_changed(None)

    def _polish_widget_tree(self, widget):
        bg = self.ui["bg"]
        surface = self.ui["surface"]
        if isinstance(widget, (tk.Frame, tk.LabelFrame)):
            widget.configure(bg=surface if widget is not self.root else bg)
            if isinstance(widget, tk.LabelFrame):
                widget.configure(
                    fg=self.ui["text"],
                    font=self.fonts["body_bold"],
                    bd=1,
                    relief=tk.SOLID,
                    highlightbackground=self.ui["border"],
                    highlightcolor=self.ui["border"],
                )
        elif isinstance(widget, tk.Label):
            current_fg = str(widget.cget("fg"))
            fg = self.ui["hint"] if current_fg in ("gray", "#666", "#555") else self.ui["text"]
            widget.configure(bg=widget.master.cget("bg"), fg=fg, font=self.fonts["body"])
        elif isinstance(widget, (tk.Radiobutton, tk.Checkbutton)):
            widget.configure(
                bg=widget.master.cget("bg"),
                fg=self.ui["text"],
                activebackground=widget.master.cget("bg"),
                activeforeground=self.ui["primary"],
                selectcolor=self.ui["surface"],
                font=self.fonts["body"],
                bd=0,
                highlightthickness=0,
                cursor="hand2",
            )
        elif isinstance(widget, ttk.Combobox):
            pass
        elif isinstance(widget, tk.Entry):
            widget.configure(
                bg=self.ui["surface"],
                fg=self.ui["text"],
                insertbackground=self.ui["primary"],
                relief=tk.SOLID,
                bd=1,
                highlightthickness=1,
                highlightbackground=self.ui["border"],
                highlightcolor=self.ui["primary"],
                font=self.fonts["body"],
            )
        elif isinstance(widget, tk.Listbox):
            widget.configure(
                bg=self.ui["surface"],
                fg=self.ui["text"],
                selectbackground="#DBEAFE",
                selectforeground=self.ui["text"],
                relief=tk.SOLID,
                bd=1,
                highlightthickness=1,
                highlightbackground=self.ui["border"],
                highlightcolor=self.ui["primary"],
                font=self.fonts["body"],
                activestyle="none",
            )
        elif isinstance(widget, tk.Scrollbar):
            widget.configure(bg=self.ui["surface_soft"], activebackground=self.ui["border"], troughcolor=self.ui["bg"])
        elif isinstance(widget, tk.Scale):
            widget.configure(
                bg=widget.master.cget("bg"),
                fg=self.ui["text"],
                activebackground=self.ui["primary"],
                troughcolor=self.ui["surface_soft"],
                highlightthickness=0,
                bd=0,
                font=self.fonts["small"],
            )
        elif isinstance(widget, tk.Button):
            bg_color = str(widget.cget("bg"))
            palette = {
                "#4CAF50": (self.ui["success"], self.ui["success_hover"]),
                "#F44336": (self.ui["danger"], self.ui["danger_hover"]),
                "#2196F3": (self.ui["primary"], self.ui["primary_hover"]),
                "#FF9800": (self.ui["warning"], self.ui["warning_hover"]),
                "#E91E63": (self.ui["pink"], self.ui["pink_hover"]),
                "#9C27B0": (self.ui["purple"], self.ui["purple_hover"]),
            }
            if bg_color in palette:
                normal, hover = palette[bg_color]
                self._style_button(widget, normal, hover, primary=(widget is getattr(self, "start_btn", None)))
            else:
                self._style_button(widget, self.ui["surface_soft"], "#E4EBF4", fg=self.ui["text"])

        for child in widget.winfo_children():
            self._polish_widget_tree(child)

    def _polish_ui(self):
        self._polish_widget_tree(self.root)
        self.header_title.configure(text_color=self.ui["text"], font=self.fonts["title"])
        self.header_subtitle.configure(text_color=self.ui["muted"], font=self.fonts["body"])
        self.header_badge.configure(text_color=self.ui["primary"], fg_color=self.ui["ink_soft"], font=self.fonts["small"])
        self.workspace_frame.configure(fg_color=self.ui["surface"], border_color=self.ui["border"])
        self.main_frame.configure(fg_color="transparent")
        self.settings_frame.configure(fg_color=self.ui["surface"], border_color=self.ui["border"])
        self.settings_title.configure(font=self.fonts["section"], text_color=self.ui["text"])
        self.settings_subtitle.configure(font=self.fonts["small"], text_color=self.ui["muted"])
        self.action_frame.configure(fg_color=self.ui["surface"], border_color=self.ui["border"])
        self.log_frame.configure(fg_color=self.ui["surface"], border_color=self.ui["border"])
        self.log_label.configure(font=self.fonts["section"], text_color=self.ui["text"])
        self.log_area.configure(
            font=self.fonts["mono"],
            fg_color=self.ui["console"],
            text_color=self.ui["console_text"],
        )
        self.progress_text.configure(font=self.fonts["small"], text_color=self.ui["muted"])
        self.status_label.configure(font=self.fonts["body_bold"])
        self.progress_bar.configure(progress_color=self.ui["primary"])
        self.refresh_start_button_text()

    def _on_mousewheel(self, event):
        """将鼠标滚轮事件转发给主滚动画布，但不拦截内部文字区域的滚动。"""
        try:
            if event.widget.winfo_class() == "Text":
                return  # CTkTextbox 内部 Text 自行处理滚动
        except Exception:
            pass
        if hasattr(self, "_scroll_canvas"):
            self._scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def setup_ui(self):
        self._setup_theme()

        # ── 可滚动主容器 ──────────────────────────────────────────
        self._scroll_canvas = tk.Canvas(
            self.root, bg=self.ui["bg"], highlightthickness=0, bd=0
        )
        self._main_scrollbar = ttk.Scrollbar(
            self.root, orient="vertical", command=self._scroll_canvas.yview
        )
        self._scroll_canvas.configure(yscrollcommand=self._main_scrollbar.set)
        self._main_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._content_frame = tk.Frame(self._scroll_canvas, bg=self.ui["bg"])
        self._canvas_win = self._scroll_canvas.create_window(
            (0, 0), window=self._content_frame, anchor="nw"
        )
        self._content_frame.bind(
            "<Configure>",
            lambda e: self._scroll_canvas.configure(
                scrollregion=self._scroll_canvas.bbox("all")
            ),
        )
        self._scroll_canvas.bind(
            "<Configure>",
            lambda e: self._scroll_canvas.itemconfig(self._canvas_win, width=e.width),
        )
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        # ─────────────────────────────────────────────────────────

        self.header_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        self.header_frame.pack(fill=tk.X)
        header_inner = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        header_inner.pack(fill=tk.X, padx=28, pady=(22, 14))
        header_text = ctk.CTkFrame(header_inner, fg_color="transparent")
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.header_title = ctk.CTkLabel(
            header_text,
            text=f"{APP_NAME} {self.version}",
            font=self.fonts["title"],
            text_color=self.ui["text"],
        )
        self.header_title.pack(anchor=tk.W)
        self.header_subtitle = ctk.CTkLabel(
            header_text,
            text=APP_DESCRIPTION,
            text_color=self.ui["muted"],
            font=self.fonts["body"],
        )
        self.header_subtitle.pack(anchor=tk.W, pady=(2, 0))
        self.header_badge = ctk.CTkLabel(
            header_inner,
            text="LOCAL AI WORKBENCH",
            font=self.fonts["small"],
            text_color=self.ui["primary"],
            fg_color=self.ui["ink_soft"],
            corner_radius=14,
            width=140,
            height=28,
        )
        self.header_badge.pack(side=tk.RIGHT)

        self.main_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        self.main_frame.pack(fill=tk.X, padx=24, pady=(6, 10), expand=False)
        
        # 使用 Notebook 分組功能
        self.workspace_frame = ctk.CTkFrame(
            self.main_frame,
            fg_color=self.ui["surface"],
            corner_radius=20,
            border_width=1,
            border_color=self.ui["border"],
        )
        self.workspace_frame.pack(fill=tk.X)
        self.current_tab_index = 0
        self.tab_names = ["YouTube 一鍵轉 KTV", "YouTube 下載", "本地影片轉 KTV", "本地音檔分離"]
        self.tabview = ctk.CTkTabview(
            self.workspace_frame,
            fg_color=self.ui["surface"],
            corner_radius=18,
            segmented_button_fg_color=self.ui["surface_soft"],
            segmented_button_selected_color=self.ui["primary"],
            segmented_button_selected_hover_color=self.ui["primary_hover"],
            segmented_button_unselected_color=self.ui["surface_soft"],
            segmented_button_unselected_hover_color="#E5F1FF",
            text_color=self.ui["text"],
            command=self._handle_ctk_tab_changed,
        )
        self.tabview.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        # --- Tab 1: YouTube 轉 MKV ---
        yt_tab = self.tabview.add(self.tab_names[0])
        
        tk.Label(yt_tab, text="YouTube 網址:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.yt_entry = ctk.CTkEntry(yt_tab, textvariable=self.yt_url_var, height=34, corner_radius=16)
        self.yt_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_entry.bind("<Button-1>", self.quick_paste_url)
        
        # 提示標籤
        tk.Label(yt_tab, text="點擊輸入框自動貼上剪貼簿網址", fg=self.ui["hint"], font=self.fonts["small"]).pack(side=tk.BOTTOM, anchor=tk.W, padx=(85, 0))

        # --- Tab 2: YouTube 純下載 ---
        yt_dl_tab = self.tabview.add(self.tab_names[1])

        # 第一列：網址輸入（Tab 2 獨立變數，不影響 Tab 1）
        dl_url_row = tk.Frame(yt_dl_tab)
        dl_url_row.pack(fill=tk.X, pady=2)
        tk.Label(dl_url_row, text="YouTube 網址:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.yt_dl_url_var = tk.StringVar()
        self.yt_dl_entry = ctk.CTkEntry(dl_url_row, textvariable=self.yt_dl_url_var, height=34, corner_radius=16)
        self.yt_dl_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.yt_dl_entry.bind("<Button-1>", self.quick_paste_dl_url)
        tk.Label(dl_url_row, text="點擊自動貼上", fg=self.ui["hint"], font=self.fonts["small"]).pack(side=tk.LEFT)

        # 第二列：下載格式 + 畫質選擇
        dl_opt_row = tk.Frame(yt_dl_tab)
        dl_opt_row.pack(fill=tk.X, pady=2)

        tk.Label(dl_opt_row, text="下載格式:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.dl_type_var = tk.StringVar(value="both")
        tk.Radiobutton(dl_opt_row, text="MP3 + MP4", variable=self.dl_type_var, value="both", font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="僅 MP3",    variable=self.dl_type_var, value="mp3",  font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="僅 MP4",    variable=self.dl_type_var, value="mp4",  font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)

        tk.Label(dl_opt_row, text="  |  MP4 畫質:", font=self.fonts["body"]).pack(side=tk.LEFT, padx=(10, 0))
        self.dl_quality_var = tk.StringVar(value="1080")
        tk.Radiobutton(dl_opt_row, text="最佳",  variable=self.dl_quality_var, value="best",  font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="1080p", variable=self.dl_quality_var, value="1080",  font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="720p",  variable=self.dl_quality_var, value="720",   font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(dl_opt_row, text="480p",  variable=self.dl_quality_var, value="480",   font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)

        # 第三列：是否進行音訊分離（Checkbox）
        dl_sep_toggle_row = tk.Frame(yt_dl_tab)
        dl_sep_toggle_row.pack(fill=tk.X, pady=(4, 0))
        self.dl_do_separate_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            dl_sep_toggle_row,
            text="下載後進行 AI 音訊分離（人聲／伴奏）",
            variable=self.dl_do_separate_var,
            command=self._toggle_dl_separate_options,
            font=self.fonts["body_bold"],
            checkbox_width=18,
            checkbox_height=18,
        ).pack(side=tk.LEFT)

        # 音訊分離選項區（預設隱藏）
        self.dl_sep_options_frame = tk.LabelFrame(yt_dl_tab, text="音訊分離設定", padx=8, pady=4)
        # 不在這裡 pack，由 _toggle_dl_separate_options 控制顯示

        # 分離選項第一列：輸出格式
        sep_fmt_row = tk.Frame(self.dl_sep_options_frame)
        sep_fmt_row.pack(fill=tk.X, pady=2)
        tk.Label(sep_fmt_row, text="分離輸出格式:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.dl_sep_format_var = tk.StringVar(value="mp3")
        for fmt_val in ["mp3", "wav", "flac"]:
            tk.Radiobutton(sep_fmt_row, text=fmt_val.upper(),
                           variable=self.dl_sep_format_var, value=fmt_val,
                           font=self.fonts["body"]).pack(side=tk.LEFT, padx=8)

        # 分離選項第二列：分離模型
        sep_model_row = tk.Frame(self.dl_sep_options_frame)
        sep_model_row.pack(fill=tk.X, pady=2)
        tk.Label(sep_model_row, text="分離模型:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.dl_sep_model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        dl_model_options = [
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏優化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品質綜合)",
            "Kim_Vocal_2.onnx (MDX - 極致人聲提取)",
            "htdemucs.yaml (Demucs - 4音軌高品質分離)",
            "htdemucs_ft.yaml (Demucs - 流行樂優化)",
        ]
        ctk.CTkComboBox(sep_model_row, variable=self.dl_sep_model_var,
                        values=dl_model_options, state="readonly", width=420, corner_radius=16,
                        font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)

        # --- Tab 3: 本地影片轉 MKV ---
        local_v_tab = self.tabview.add(self.tab_names[2])

        # 字幕列先 pack (side=BOTTOM)，讓 v_top_frame expand 時能正確撐滿剩餘空間
        v_sub_row = tk.Frame(local_v_tab)
        v_sub_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        v_top_frame = tk.Frame(local_v_tab)
        v_top_frame.pack(fill=tk.BOTH, expand=True)

        v_list_frame = tk.Frame(v_top_frame)
        v_list_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self.v_listbox = tk.Listbox(v_list_frame, height=4, selectmode=tk.EXTENDED)
        self.v_listbox.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        v_scrollbar = tk.Scrollbar(v_list_frame)
        v_scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.v_listbox.config(yscrollcommand=v_scrollbar.set)
        v_scrollbar.config(command=self.v_listbox.yview)

        v_btn_frame = tk.Frame(v_top_frame)
        v_btn_frame.pack(side=tk.RIGHT, padx=5, fill=tk.Y)
        self.v_list = []
        ctk.CTkButton(v_btn_frame, text="加入影片",   command=self.browse_local_video,     width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)
        ctk.CTkButton(v_btn_frame, text="加入資料夾", command=self.browse_local_v_folder,   width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)
        ctk.CTkButton(v_btn_frame, text="移除選取",   command=self.remove_selected_v,       width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)
        ctk.CTkButton(v_btn_frame, text="清除清單",   command=self.clear_v_list,            width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)

        # 字幕導入列
        tk.Label(v_sub_row, text="字幕檔:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.local_subtitle_var = tk.StringVar()
        ctk.CTkEntry(v_sub_row, textvariable=self.local_subtitle_var, height=32, corner_radius=16).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ctk.CTkButton(v_sub_row, text="選擇 SRT", command=self.browse_local_subtitle, width=86, height=30, corner_radius=14, font=self.fonts["button"]).pack(side=tk.LEFT)
        ctk.CTkButton(v_sub_row, text="清除", command=lambda: self.local_subtitle_var.set(""), width=58, height=30, corner_radius=14, font=self.fonts["button"]).pack(side=tk.LEFT, padx=3)
        tk.Label(v_sub_row, text="空白則自動比對影片同名 .srt", fg=self.ui["hint"], font=self.fonts["small"]).pack(side=tk.LEFT, padx=4)

        # --- Tab 4: 本地檔案分離 ---
        file_tab = self.tabview.add(self.tab_names[3])
        
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
        ctk.CTkButton(file_btn_frame, text="加入檔案", command=self.browse_file,            width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)
        ctk.CTkButton(file_btn_frame, text="移除選取", command=self.remove_selected_file,  width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)
        ctk.CTkButton(file_btn_frame, text="清除清單", command=self.clear_files,           width=96, height=30, corner_radius=14, font=self.fonts["button"]).pack(pady=2)
        
        # --- 設定區 ---
        self.settings_expanded = tk.BooleanVar(value=False)
        self.settings_frame = ctk.CTkFrame(
            self.main_frame,
            fg_color=self.ui["surface"],
            corner_radius=20,
            border_width=1,
            border_color=self.ui["border"],
        )
        self.settings_frame.pack(fill=tk.X, pady=(10, 0))
        settings_frame = self.settings_frame
        settings_header = ctk.CTkFrame(settings_frame, fg_color="transparent")
        settings_header.pack(fill=tk.X, padx=16, pady=12)
        self.settings_title = ctk.CTkLabel(settings_header, text="核心設定", font=self.fonts["section"], text_color=self.ui["text"])
        self.settings_title.pack(side=tk.LEFT)
        self.settings_subtitle = ctk.CTkLabel(settings_header, text="模型、輸出、字幕與運算環境", text_color=self.ui["muted"], font=self.fonts["small"])
        self.settings_subtitle.pack(side=tk.LEFT, padx=(10, 0))
        self.settings_toggle_btn = ctk.CTkButton(settings_header, text="展開", command=self._toggle_settings_panel, width=72, height=30, corner_radius=14, font=self.fonts["button"])
        self.settings_toggle_btn.pack(side=tk.RIGHT)
        self.settings_dropdown = ctk.CTkScrollableFrame(settings_frame, fg_color=self.ui["surface"], height=118, corner_radius=0)
        self.settings_body = self.settings_dropdown
        
        # 輸出目錄
        out_row = tk.Frame(self.settings_body)
        out_row.pack(fill=tk.X, pady=2)
        self.output_dir_var = tk.StringVar(value=str(self.app_dir / "output"))
        tk.Label(out_row, text="輸出目錄:", font=self.fonts["body"]).pack(side=tk.LEFT)
        ctk.CTkEntry(out_row, textvariable=self.output_dir_var, height=32, corner_radius=16).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ctk.CTkButton(out_row, text="瀏覽", command=self.browse_output_dir, width=72, height=30, corner_radius=14, font=self.fonts["button"]).pack(side=tk.RIGHT)
        
        # Cookie 設定列（防止 YouTube 429 封鎖）
        cookie_row = tk.Frame(self.settings_body)
        cookie_row.pack(fill=tk.X, pady=2)
        tk.Label(cookie_row, text="YouTube Cookie:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.cookie_browser_var = tk.StringVar(value="none")
        tk.Radiobutton(cookie_row, text="不使用", variable=self.cookie_browser_var, value="none",    font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Chrome",  variable=self.cookie_browser_var, value="chrome", font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Firefox", variable=self.cookie_browser_var, value="firefox",font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Edge",    variable=self.cookie_browser_var, value="edge",   font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Radiobutton(cookie_row, text="Brave",   variable=self.cookie_browser_var, value="brave",  font=self.fonts["body"]).pack(side=tk.LEFT, padx=4)
        tk.Label(cookie_row, text="遇到 429 封鎖時，選擇你目前登入 YouTube 的瀏覽器", fg=self.ui["hint"], font=self.fonts["small"]).pack(side=tk.LEFT, padx=6)

        # 運算裝置與去噪
        self.opt_row = tk.Frame(self.settings_body)
        opt_row = self.opt_row
        opt_row.pack(fill=tk.X, pady=5)
        
        tk.Label(opt_row, text="運算裝置:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.device_var = tk.StringVar(value="cpu")
        tk.Radiobutton(opt_row, text="CPU",          variable=self.device_var, value="cpu", font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(opt_row, text="GPU (NVIDIA)", variable=self.device_var, value="gpu", font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)

        ctk.CTkButton(opt_row, text="檢測 GPU 環境", command=self.check_gpu_env, width=116, height=30, corner_radius=14, font=self.fonts["button"]).pack(side=tk.LEFT, padx=10)

        self.denoise_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(opt_row, text="啟用 AI 去噪", variable=self.denoise_var, checkbox_width=18, checkbox_height=18, font=self.fonts["body"]).pack(side=tk.RIGHT, padx=10)

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
        self.format_row = tk.Frame(self.settings_body)
        format_row = self.format_row
        format_row.pack(fill=tk.X, pady=2)
        
        tk.Label(format_row, text="AI 模型:", font=self.fonts["body"]).pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="UVR-MDX-NET-Inst_HQ_3.onnx")
        model_options = [
            "UVR-MDX-NET-Inst_HQ_3.onnx (MDX - 伴奏優化)",
            "UVR-MDX-NET-Inst_HQ_4.onnx (MDX - 高品質綜合)",
            "Kim_Vocal_2.onnx (MDX - 極致人聲提取)",
            "htdemucs.yaml (Demucs - 4音軌高品質分離)",
            "htdemucs_ft.yaml (Demucs - 流行樂優化)",
            "htdemucs_6s.yaml (Demucs - 6音軌擴充版)",
        ]
        self.model_menu = ctk.CTkComboBox(format_row, variable=self.model_var, values=model_options, state="readonly", width=420, corner_radius=16, font=self.fonts["body"])
        self.model_menu.pack(side=tk.LEFT, padx=5)
        self.model_menu.set(model_options[0])

        tk.Label(format_row, text="輸出格式:", font=self.fonts["body"]).pack(side=tk.LEFT, padx=(10, 0))
        self.output_format_var = tk.StringVar(value="mp3")
        for fmt in ["mp3", "wav", "flac"]:
            tk.Radiobutton(format_row, text=fmt.upper(), variable=self.output_format_var, value=fmt, font=self.fonts["body"]).pack(side=tk.LEFT, padx=10)

        # KTV 影片設定列
        self.ktv_row = tk.Frame(self.settings_body)
        ktv_row = self.ktv_row
        ktv_row.pack(fill=tk.X, pady=2)

        tk.Label(ktv_row, text="KTV 影片格式:", font=self.fonts["body"]).pack(side=tk.LEFT)
        tk.Radiobutton(ktv_row, text="MKV（預設，相容性最佳）",
                       variable=self.video_format_var, value="mkv", font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(ktv_row, text="MP4",
                       variable=self.video_format_var, value="mp4", font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)

        # 伴唱帶音軌模式列
        self.track_row = tk.Frame(self.settings_body)
        track_row = self.track_row
        track_row.pack(fill=tk.X, pady=2)

        tk.Label(track_row, text="伴唱帶音軌:", font=self.fonts["body"]).pack(side=tk.LEFT)
        tk.Radiobutton(track_row, text="雙音軌（伴唱＋人聲，預設）",
                       variable=self.audio_track_mode_var, value="dual", font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(track_row, text="左伴唱／右人聲（單音軌立體聲）",
                       variable=self.audio_track_mode_var, value="lr",   font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)

        # 導唱混合比例列
        self.mix_row = tk.Frame(self.settings_body)
        mix_row = self.mix_row
        mix_row.pack(fill=tk.X, pady=2)
        tk.Label(mix_row, text="導唱混合比例:", font=self.fonts["body"]).pack(side=tk.LEFT)
        tk.Scale(
            mix_row,
            from_=0, to=100,
            orient=tk.HORIZONTAL,
            showvalue=False,
            resolution=5,
            length=180,
            variable=self.vocal_mix_var,
            command=lambda _value: self.update_vocal_mix_label(),
            font=self.fonts["small"],
        ).pack(side=tk.LEFT, padx=5)
        tk.Label(mix_row, textvariable=self.vocal_mix_label_var, width=28, anchor="w", font=self.fonts["body"]).pack(side=tk.LEFT, padx=5)
        tk.Label(mix_row, text="人聲越高，越適合跟唱練習", fg=self.ui["hint"], font=self.fonts["small"]).pack(side=tk.LEFT, padx=5)
        self.update_vocal_mix_label()

        # 額外影片選項列
        self.extra_video_row = tk.Frame(self.settings_body)
        extra_video_row = self.extra_video_row
        extra_video_row.pack(fill=tk.X, pady=2)

        self.force_1080p_var = tk.BooleanVar(value=False)
        self.force_1080p_chk = ctk.CTkCheckBox(
            extra_video_row,
            text="強制等比輸出 1080p（不足自動補黑邊）",
            variable=self.force_1080p_var,
            checkbox_width=18,
            checkbox_height=18,
            font=self.fonts["body"],
        )
        self.force_1080p_chk.pack(side=tk.LEFT)

        self.yt_cc_var = tk.BooleanVar(value=True)
        self.yt_cc_chk = ctk.CTkCheckBox(
            extra_video_row,
            text="啟用 YouTube CC 字幕處理",
            variable=self.yt_cc_var,
            command=self.refresh_yt_subtitle_mode_ui,
            checkbox_width=18,
            checkbox_height=18,
            font=self.fonts["body"],
        )
        self.yt_cc_chk.pack(side=tk.LEFT, padx=(12, 0))

        self.yt_subtitle_mode_var = tk.StringVar(value="mux")
        self.yt_subtitle_mode_row = tk.Frame(self.settings_body)
        tk.Label(self.yt_subtitle_mode_row, text="字幕模式:", font=self.fonts["body"]).pack(side=tk.LEFT)
        tk.Radiobutton(
            self.yt_subtitle_mode_row,
            text="下載 SRT 字幕",
            variable=self.yt_subtitle_mode_var,
            value="srt_only",
            command=self.refresh_yt_subtitle_mode_ui,
            font=self.fonts["body"],
        ).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(
            self.yt_subtitle_mode_row,
            text="下載 SRT 並封裝入影片",
            variable=self.yt_subtitle_mode_var,
            value="mux",
            command=self.refresh_yt_subtitle_mode_ui,
            font=self.fonts["body"],
        ).pack(side=tk.LEFT, padx=5)

        # 按鈕區
        self.action_frame = ctk.CTkFrame(
            self._content_frame,
            fg_color=self.ui["surface"],
            corner_radius=20,
            border_width=1,
            border_color=self.ui["border"],
        )
        self.action_frame.pack(pady=(0, 8), fill=tk.X, padx=24)
        btn_frame = self.action_frame
        self.start_btn = ctk.CTkButton(btn_frame, text="開始分離任務",       command=self.on_start_click,     width=180, height=36, corner_radius=16, font=self.fonts["button"])
        self.start_btn.config = self.start_btn.configure
        self.start_btn.pack(side=tk.LEFT, padx=(16, 10), pady=12)
        self.cancel_btn = ctk.CTkButton(btn_frame, text="取消任務",           command=self.cancel_processing,  width=140, height=36, corner_radius=16, font=self.fonts["button"], state=tk.DISABLED)
        self.cancel_btn.config = self.cancel_btn.configure
        self.cancel_btn.pack(side=tk.LEFT, padx=5, pady=12)
        self.update_btn = ctk.CTkButton(btn_frame, text="一鍵修復/初始化環境", command=self.check_components,   width=180, height=36, corner_radius=16, font=self.fonts["button"])
        self.update_btn.config = self.update_btn.configure
        self.update_btn.pack(side=tk.LEFT, padx=10, pady=12)
        
        # 狀態與進度
        self.status_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        self.status_frame.pack(fill=tk.X, padx=24)
        self.status_var = tk.StringVar(value="狀態: 初始化中...")
        self.status_label = ctk.CTkLabel(self.status_frame, textvariable=self.status_var, text_color=self.ui["primary"], font=self.fonts["body_bold"])
        self.status_label.pack(side=tk.LEFT)
        
        self.progress_text = ctk.CTkLabel(self.status_frame, text="0%", font=self.fonts["small"], text_color=self.ui["muted"])
        self.progress_text.pack(side=tk.RIGHT)
        
        self.progress_bar = ctk.CTkProgressBar(self._content_frame, height=10, corner_radius=8, progress_color=self.ui["primary"])
        self.progress_bar.set(0)
        self.progress_bar.pack(fill=tk.X, padx=24, pady=(5, 10))
        
        # 日誌區
        self.log_frame = ctk.CTkFrame(
            self._content_frame,
            fg_color=self.ui["surface"],
            corner_radius=20,
            border_width=1,
            border_color=self.ui["border"],
        )
        self.log_frame.pack(pady=(0, 16), padx=24, fill=tk.X)
        self.log_label = ctk.CTkLabel(self.log_frame, text="執行日誌", font=self.fonts["section"], text_color=self.ui["text"])
        self.log_label.pack(anchor=tk.W, padx=14, pady=(12, 6))
        self.log_area = ctk.CTkTextbox(
            self.log_frame,
            height=220,
            font=self.fonts["mono"],
            fg_color=self.ui["console"],
            text_color=self.ui["console_text"],
            corner_radius=16,
            border_width=0,
        )
        self.log_area.pack(fill=tk.X, padx=14, pady=(0, 12))

        # 立即刷新視窗並顯示歡迎訊息
        self.refresh_yt_subtitle_mode_ui()
        self.root.update_idletasks()
        self.show_welcome_message()

    def refresh_start_button_text(self):
        """依當前分頁與字幕模式更新主按鈕文字。"""
        current_tab = self.current_tab_index
        vfmt = self.video_format_var.get().upper() if hasattr(self, 'video_format_var') else "MKV"
        if current_tab == 0:
            self._set_action_button(f"一鍵製作 {vfmt} 伴唱帶", self.ui["pink"], self.ui["pink_hover"])
        elif current_tab == 1:
            self._set_action_button("立即下載 YouTube 檔案", self.ui["primary"], self.ui["primary_hover"])
        elif current_tab == 2:
            self._set_action_button(f"製作本地影片 KTV ({vfmt})", self.ui["purple"], self.ui["purple_hover"])
        else:
            self._set_action_button("開始批量分離音檔", self.ui["success"], self.ui["success_hover"])

    def refresh_yt_subtitle_mode_ui(self):
        """依分頁與勾選狀態顯示字幕模式列。"""
        current_tab = self.current_tab_index
        if current_tab == 0 and self.yt_cc_var.get():
            self.yt_subtitle_mode_row.pack(fill=tk.X, pady=2)
        else:
            self.yt_subtitle_mode_row.pack_forget()
        if hasattr(self, "start_btn"):
            self.refresh_start_button_text()

    def _toggle_dl_separate_options(self):
        """根據音訊分離 Checkbox 顯示或隱藏分離設定區"""
        if self.dl_do_separate_var.get():
            self.dl_sep_options_frame.pack(fill=tk.X, pady=(2, 4), padx=0)
        else:
            self.dl_sep_options_frame.pack_forget()

    def on_tab_changed(self, event):
        """當分頁切換時，自動更新啟動按鈕文字，並顯示/隱藏對應的核心設定列"""
        current_tab = self.current_tab_index

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
        current_tab = self.current_tab_index
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

    def browse_local_subtitle(self):
        path = filedialog.askopenfilename(
            title="選擇字幕檔",
            filetypes=[("SRT 字幕", "*.srt"), ("所有檔案", "*.*")]
        )
        if path:
            self.local_subtitle_var.set(path)

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
        self.log_area.delete(1.0, tk.END)
        self.runner.start(self.local_v_batch_process,
                          status_text="正在進行批次影片處理...",
                          start_btn=self.start_btn, cancel_btn=self.cancel_btn)

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
                extract_result = self.ffmpeg_svc.extract_audio(video_path, str(temp_audio))
                if not extract_result.success:
                    self.log(f"  ❌ 音訊擷取失敗: {extract_result.error}")
                    continue

                # 2. 執行分離
                self.update_progress(progress_base + int(progress_step * 0.3), f"正在 AI 分離 ({i+1}/{total})")
                sep_result = self.sep_svc.run_audio_separator(
                    str(temp_audio), output_dir,
                    fmt=self.output_format_var.get(),
                    device_str=self.device_var.get(),
                    model=self.model_var.get(),
                    overlap=float(self.overlap_var.get()),
                    denoise=self.denoise_var.get(),
                )

                if sep_result.success:
                    self.log("  > 正在整理產出檔案...")
                    voc_file, inst_file = self.sep_svc.consolidate_stems(
                        str(temp_audio), video_path, output_dir,
                        fmt=self.output_format_var.get(),
                    )

                    if voc_file and inst_file:
                        # 3. 合成 KTV 影片
                        vfmt = self.video_format_var.get()
                        self.update_progress(progress_base + int(progress_step * 0.8), f"正在合成 {vfmt.upper()} ({i+1}/{total})")
                        output_file = Path(output_dir) / f"{video_stem}_KTV.{vfmt}"
                        vocal_mix = max(0.0, min(1.0, float(self.vocal_mix_var.get()) / 100.0))

                        # 字幕：優先使用手動選擇；否則自動比對影片同名 .srt
                        manual_sub = self.local_subtitle_var.get().strip()
                        if manual_sub and os.path.exists(manual_sub):
                            subtitle_for_video = manual_sub
                        else:
                            auto_sub = Path(video_path).parent / f"{video_stem}.srt"
                            subtitle_for_video = str(auto_sub) if auto_sub.exists() else None
                        if subtitle_for_video:
                            self.log(f"  > 套用字幕: {os.path.basename(subtitle_for_video)}")

                        ktv_result = self.ffmpeg_svc.build_ktv_video(
                            video=video_path, vocal=voc_file, instrumental=inst_file,
                            subtitle=subtitle_for_video, output=str(output_file), fmt=vfmt,
                            track_mode=self.audio_track_mode_var.get(),
                            vocal_mix=vocal_mix,
                            force_1080p=self.force_1080p_var.get(),
                        )
                        if ktv_result.success:
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
        self.log_area.delete(1.0, tk.END)
        self.runner.start(self.pure_download_process, url,
                          status_text="正在下載 YouTube 檔案...",
                          start_btn=self.start_btn, cancel_btn=self.cancel_btn)

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
            video_id = self.dl_svc.extract_youtube_video_id(url) or None

            mp4_result: TaskResult | None = None
            mp3_result: TaskResult | None = None

            if dl_type in ["both", "mp4"]:
                self.log("  > 正在下載 MP4...")
                mp4_result = self.dl_svc.pure_download_file(url, output_dir, "mp4", quality, video_id=video_id)

            if dl_type in ["both", "mp3"]:
                self.log("  > 正在下載 MP3...")
                mp3_result = self.dl_svc.pure_download_file(url, output_dir, "mp3", quality, video_id=video_id)

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
                    sep_result = self.sep_svc.run_audio_separator(
                        target_mp3, output_dir,
                        fmt=self.dl_sep_format_var.get(),
                        device_str=self.device_var.get(),
                        model=self.dl_sep_model_var.get(),
                        overlap=float(self.overlap_var.get()),
                        denoise=self.denoise_var.get(),
                    )
                    if sep_result.success:
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

    # ------------------------------------------------------------------
    # File logger
    # ------------------------------------------------------------------

    def _setup_file_logger(self) -> logging.Logger:
        log_dir = self.app_dir / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        logger = logging.getLogger("vocalforge")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            try:
                fh = RotatingFileHandler(
                    log_dir / "debug.log",
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                fh.setFormatter(logging.Formatter(
                    "%(asctime)s [%(levelname)-5s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                ))
                logger.addHandler(fh)
            except Exception:
                pass
        logger.info("=" * 60)
        logger.info(f"Session Start: {APP_NAME} {APP_VERSION}")
        logger.info(f"app_dir: {self.app_dir}")
        logger.info(f"py_dir : {self.py_dir}")
        logger.info(f"lib_dir: {self.lib_dir}")
        return logger

    def debug_log(self, message: str) -> None:
        """寫入 DEBUG 等級訊息，僅記錄到檔案，不顯示於 GUI。"""
        if hasattr(self, "_file_logger"):
            self._file_logger.debug(message)

    def log(self, message):
        self.root.after(0, lambda: self._safe_log(message))

    def _safe_log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        if hasattr(self, "_file_logger"):
            self._file_logger.info(message)

    def update_progress(self, percent, text=None):
        self.root.after(0, lambda: self._safe_update_progress(percent, text))

    def _safe_update_progress(self, percent, text):
        if isinstance(self.progress_bar, ctk.CTkProgressBar):
            self.progress_bar.set(max(0, min(100, percent)) / 100)
        else:
            self.progress_bar['value'] = percent
        self.progress_text.configure(text=f"{percent}%")
        if text:
            self.status_var.set(f"狀態: {text} ({percent}%)")

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
        color_map = {
            "blue": self.ui["primary"],
            "green": self.ui["success"],
            "orange": self.ui["warning"],
            "red": self.ui["danger"],
        }
        if isinstance(self.status_label, ctk.CTkLabel):
            self.status_label.configure(text_color=color_map.get(color, color))
        else:
            self.status_label.config(fg=color_map.get(color, color))

    def check_gpu_env(self):
        self.log("\n---[開始 GPU 環境深度檢測] ---")

        # 先確認硬體層面是否有啟用的 NVIDIA GPU
        # 若無（例如筆電拔電源後切換至內顯），直接告知使用者，不進行後續修復流程
        if not self.env_svc._is_nvidia_gpu_present():
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
        env = self.env_svc.build_python_env(gpu_lib_dir, include_gpu_runtime=True)
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

    def _start_async_setup(self, install_mode: str = "auto"):
        self.runner.start(
            self.env_svc.async_setup_environment, install_mode,
            status_text="正在部署可攜式環境...",
            start_btn=getattr(self, "start_btn", None),
            cancel_btn=getattr(self, "cancel_btn", None),
        )

    def check_components(self, prompt=True):
        if self.is_processing:
            return
        self.env_svc.check_components(prompt=prompt)

    def start_separation(self):
        if not self.file_list:
            messagebox.showwarning("警告", "請先加入音檔！")
            return
        if self.is_processing: return

        # 如果選用 GPU 但環境尚未檢測或不完全，先提示檢測
        if self.device_var.get() == "gpu":
            self.log("🚀 啟動前檢查 GPU 環境...")
            # 這裡不彈出視窗，直接執行背景檢測
            if not self.env_svc._quick_check_gpu():
                if messagebox.askyesno("環境未就緒", "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？\n(若不修復將改用 CPU 運行，速度較慢)"):
                    self.check_gpu_env()
                    return
                else:
                    self.log("⚠️ 使用者選擇忽略，將嘗試改用 CPU 模式。")
                    self.device_var.set("cpu")

        self.is_processing = True
        self.log_area.delete(1.0, tk.END)
        self.runner.start(self.batch_process,
                          status_text="正在處理中...",
                          start_btn=self.start_btn, cancel_btn=self.cancel_btn)

    def start_yt_process(self):
        url = self.yt_url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "請輸入 YouTube 網址！")
            return
        if self.is_processing: return

        # 如果選用 GPU 但環境尚未檢測或不完全，先提示檢測
        if self.device_var.get() == "gpu":
            if not self.env_svc._quick_check_gpu():
                if messagebox.askyesno("環境未就緒", "偵測到您的 GPU 環境尚未配置完成，是否現在進行一鍵修復？"):
                    self.check_gpu_env()
                    return
                else:
                    self.device_var.set("cpu")

        self.is_processing = True
        self.log_area.delete(1.0, tk.END)
        self.runner.start(self.yt_process, url,
                          status_text="正在從 YouTube 下載並處理...",
                          start_btn=self.start_btn, cancel_btn=self.cancel_btn)

    def yt_process(self, url):
        try:
            output_dir = self.output_dir_var.get()
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            self.dl_svc.last_downloaded_subtitle = None
            subtitle_mode = self.yt_subtitle_mode_var.get() if self.yt_cc_var.get() else "none"

            self.log(f"--- 正在處理 YouTube 影片: {url} ---")
            self.update_progress(10, "正在獲取影片資訊")
            if subtitle_mode == "srt_only":
                self.log("📝 字幕模式：只抓 SRT，不封裝進成品")
            elif subtitle_mode == "mux":
                self.log("📝 字幕模式：抓字幕並合成到成品")

            # 1. 僅下載 MP4 (影像+音訊)，節省頻寬
            video_file = self.dl_svc.download_youtube(
                url,
                output_dir,
                mode="mp4",
                download_subtitles=(subtitle_mode in ("srt_only", "mux")),
            )

            if not video_file:
                self.log("❌ YouTube 影片下載失敗。")
                return

            # 2. 從 MP4 中擷取 MP3 音訊進行分離，避免二次下載
            self.update_progress(30, "正在從影片擷取音訊")
            self.log("  > 正在從下載的影片中擷取音訊...")
            video_path = Path(video_file)
            audio_file = str(video_path.parent / f"{video_path.stem}_audio.mp3")

            extract_result = self.ffmpeg_svc.extract_audio(video_file, audio_file)
            if not extract_result.success:
                self.log(f"  ❌ 音訊擷取失敗: {extract_result.error}")
                return
            self.log(f"  ✅ 音訊擷取完成: {os.path.basename(audio_file)}")

            self.update_progress(40, "正在分離人聲與伴奏")

            # 3. 執行分離
            sep_result = self.sep_svc.run_audio_separator(
                audio_file, output_dir,
                fmt=self.output_format_var.get(),
                device_str=self.device_var.get(),
                model=self.model_var.get(),
                overlap=float(self.overlap_var.get()),
                denoise=self.denoise_var.get(),
            )

            if sep_result.success:
                self.log("📦 正在整理並重新命名產出檔案...")
                voc_file, inst_file = self.sep_svc.consolidate_stems(
                    audio_file, video_file, output_dir,
                    fmt=self.output_format_var.get(),
                )

                if voc_file and inst_file:
                    # 4. 合成 KTV 影片
                    vfmt = self.video_format_var.get()
                    self.update_progress(80, f"正在合成 {vfmt.upper()} 伴唱帶")
                    output_file = Path(output_dir) / f"{Path(video_file).stem}_KTV.{vfmt}"
                    subtitle_for_mux = self.dl_svc.last_downloaded_subtitle if subtitle_mode == "mux" else None
                    vocal_mix = max(0.0, min(1.0, float(self.vocal_mix_var.get()) / 100.0))
                    ktv_result = self.ffmpeg_svc.build_ktv_video(
                        video=video_file, vocal=voc_file, instrumental=inst_file,
                        subtitle=subtitle_for_mux, output=str(output_file),
                        fmt=vfmt, track_mode=self.audio_track_mode_var.get(),
                        vocal_mix=vocal_mix, force_1080p=self.force_1080p_var.get(),
                    )

                    if ktv_result.success:
                        self.log(f"✅ 成功生成 {vfmt.upper()} 伴唱帶: {output_file.name}")
                        if self.dl_svc.last_downloaded_subtitle:
                            if subtitle_for_mux and vfmt.upper() == "MKV":
                                # 字幕已封裝進 MKV，刪除旁掛 SRT 避免播放器重複載入兩條字幕
                                srt_path = self.dl_svc.last_downloaded_subtitle
                                try:
                                    if os.path.exists(srt_path):
                                        os.remove(srt_path)
                                        self.log(f"  🗑️ 字幕已封裝於 MKV 內，移除旁掛 SRT：{os.path.basename(srt_path)}")
                                except Exception:
                                    pass
                                self.dl_svc.last_downloaded_subtitle = None
                            else:
                                self.dl_svc.last_downloaded_subtitle = self.dl_svc.align_subtitle_filename(
                                    self.dl_svc.last_downloaded_subtitle, str(output_file)
                                )
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

                sep_result = self.sep_svc.run_audio_separator(
                    input_file, output_dir,
                    fmt=self.output_format_var.get(),
                    device_str=self.device_var.get(),
                    model=self.model_var.get(),
                    overlap=float(self.overlap_var.get()),
                    denoise=self.denoise_var.get(),
                )

                if sep_result.success:
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

if __name__ == "__main__":
    root = ctk.CTk()
    app = VocalForgeStudioApp(root)
    root.mainloop()

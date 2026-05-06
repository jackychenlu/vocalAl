import subprocess
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable


class TaskRunner:
    """統一任務生命週期：啟動、取消、finish_processing。

    所有 UI 操作透過 root.after 在主執行緒執行。
    呼叫端只需呼叫 start() 並提供 target_fn，不需手動管理 is_running / cancel_event。
    """

    def __init__(
        self,
        root: tk.Tk,
        log_fn: Callable[[str], None],
        progress_fn: Callable[[int, str], None],
        status_fn: Callable[[str, str], None],
    ):
        self.root = root
        self._log = log_fn
        self._progress = progress_fn
        self._status = status_fn
        self.is_running = False
        self.cancel_event = threading.Event()
        self.current_process: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        target_fn: Callable,
        *args,
        status_text: str = "處理中...",
        start_btn: tk.Widget | None = None,
        cancel_btn: tk.Widget | None = None,
    ) -> bool:
        """啟動背景任務。若已有任務執行中則忽略並回傳 False。"""
        if self.is_running:
            return False
        self.is_running = True
        self.cancel_event.clear()
        if start_btn:
            start_btn.config(state=tk.DISABLED)
        if cancel_btn:
            cancel_btn.config(state=tk.NORMAL)
        self._status(status_text, "orange")

        def _run():
            try:
                target_fn(*args)
            except Exception as e:
                self._log(f"❌ 未預期錯誤: {e}")
            finally:
                self._finish(start_btn, cancel_btn)

        threading.Thread(target=_run, daemon=True).start()
        return True

    def cancel(self) -> None:
        """要求中止當前任務。"""
        if not self.is_running:
            return
        self.cancel_event.set()
        proc = self.current_process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                self._log("⚠️ 已發送中止訊號給子程序...")
            except Exception as e:
                self._log(f"⚠️ 中止子程序時出錯: {e}")
        self._log("🛑 使用者已取消任務。")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _finish(
        self,
        start_btn: tk.Widget | None,
        cancel_btn: tk.Widget | None,
    ) -> None:
        self.is_running = False
        self.cancel_event.clear()
        self.current_process = None
        if start_btn:
            self.root.after(0, lambda: start_btn.config(state=tk.NORMAL))
        if cancel_btn:
            self.root.after(0, lambda: cancel_btn.config(state=tk.DISABLED))
        self._status("準備就緒", "green")

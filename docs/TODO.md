# 待辦清單

## 長期追蹤（暫不優先）

- 支援命令列參數或拖曳檔案到 EXE
- 將下載、分離、合成流程寫成可測試的純函式（去除 Tkinter 依賴）
- 為 yt-dlp / FFmpeg / audio-separator 建立 dry-run 或 debug log 模式
- 檢查路徑含中文、空白、特殊符號時所有 subprocess 行為
- 檢查所有清理流程，避免失敗時刪掉仍需要的中間檔
- `EnvironmentService` 的 `messagebox` 改為 callback，提升可測試性
- `is_processing` 旗標加 `threading.Lock` 保護

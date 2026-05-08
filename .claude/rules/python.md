# Python 編碼規則

適用於 `vocalforge_ktv_studio.py` 和 `services/` 目錄下的所有 Python 檔案。

## 強制規則

- 使用 `py` 啟動器（非 `python` 或 `python3`）執行 Python
- `subprocess.run()` 永遠使用 list 形式，禁止 `shell=True`
- 所有 public 方法需有型別注解；union 使用 `X | Y` 語法（Python 3.10+）
- 服務方法回傳 `TaskResult(success=bool, path=..., error=...)`
- 檔案讀寫使用 `encoding="utf-8"`（無 BOM），不用 `encoding="utf-8-sig"`
- Tempfile 建立後必須 `unlink()` 或用 context manager

## 禁止行為

- 禁止修改 `runtime_python/`, `ai_libraries/`, `ai_libraries_gpu/`, `engine_ffmpeg/` 目錄內的檔案
- 禁止在 `except` 區塊中靜默忽略所有例外（`except Exception: pass`）—— 至少要 log
- 禁止在 GUI 主執行緒中執行阻塞操作（使用 `TaskRunner` 或 `threading.Thread`）

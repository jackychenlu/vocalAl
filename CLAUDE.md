# VocalForge KTV Studio — 專案規範

> Windows Python 桌面應用，提供 YouTube 下載、AI 人聲分離（Demucs/UVR）、FFmpeg KTV 影片合成。
> 版本：v2.11.0 | 語言：Python 3.11+ | 平台：Windows 11

## 執行與開發指令

```powershell
# 啟動應用程式
py vocalforge_ktv_studio.py

# Lint（排除 runtime 與 vendor 目錄）
py -m ruff check . --exclude runtime_python,ai_libraries,ai_libraries_gpu,ai_models,engine_ffmpeg,output,build,dist
py -m ruff format . --check --exclude runtime_python,ai_libraries,ai_libraries_gpu,ai_models,engine_ffmpeg,output,build,dist

# 靜態型別檢查
py -m pyright vocalforge_ktv_studio.py services/
```

自訂 skills（輸入 `/` 觸發）：`/lint`、`/build`、`/test`、`/deps`、`/debug`、`/smart-debug`、`/security-scan`、`/tech-debt`、`/refactor-clean`
定義於 `.claude/skills/`（新格式，取代舊 `commands/`）

## 專案架構

```
vocalforge_ktv_studio.py   # 主 GUI 應用（VocalForgeStudioApp 類別）
services/
  task_result.py           # TaskResult dataclass（所有服務的回傳型別）
  ffmpeg_service.py        # FFmpeg 指令封裝（extract_audio, merge_stems, build_ktv_video）
  download_service.py      # yt-dlp 下載 + SRT 清理
  separation_service.py    # Demucs/UVR AI 分離
  environment_service.py   # 環境初始化、工具下載
  task_runner.py           # 背景執行緒任務管理
```

### 執行時目錄（不進版本控制，不打包）

| 目錄 | 用途 |
|------|------|
| `engine_ffmpeg/` | FFmpeg 執行檔 |
| `runtime_python/` | 內建 Python 核心 |
| `ai_libraries/` | CPU AI 套件 |
| `ai_libraries_gpu/` | GPU AI 套件 |
| `ai_models/` | Demucs/UVR 模型 |
| `output/` | 所有輸出檔案 |

## 編碼規範

- **Python 版本**：3.11+，使用 `py` 啟動器（非 `python`）
- **型別注解**：所有 public 方法參數與回傳值都需標注；`Callable | None` 使用 union 語法
- **服務回傳**：統一使用 `TaskResult(success=bool, path=str|None, error=str|None)`
- **Subprocess**：永遠使用 list 形式，禁止 `shell=True`
- **Tempfile**：建立後必須用 `unlink()` 或 context manager 清理
- **字串編碼**：檔案 I/O 使用 `encoding="utf-8"`（無 BOM），不用 `utf-8-sig`
- **Import 排序**：stdlib → third-party → local（ruff 自動管理）

## UI 規範（tkinter + customtkinter 混合）

### 字體階層（`self.fonts`）

| 鍵 | 規格 | 用途 |
|----|------|------|
| `title` | Segoe UI 20 Bold | 頁面標題 |
| `section` | Segoe UI 12 Bold | 區塊標題 |
| `body` | Segoe UI 10 | 一般文字、Label |
| `body_bold` | Segoe UI 10 Bold | 強調文字 |
| `button` | Segoe UI 10 Bold | 所有按鈕 |
| `small` | Segoe UI 9 | 說明文字、status |
| `mono` | Consolas 10 | log 輸出 |

所有 widget 必須明確傳入 `font=self.fonts["..."]`，不依賴系統預設。

### 語意色彩（`self.ui`）

| 鍵 | 顏色 | 用途 |
|----|------|------|
| `primary` | #007AFF | 主操作按鈕 |
| `success` | #34C759 | 確認、完成 |
| `danger` | #FF3B30 | 取消、刪除、錯誤 |
| `warning` | #FF9500 | 警告、注意 |
| `purple` | #AF52DE | AI 分離相關 |
| `pink` | #FF2D55 | KTV 合成相關 |

### 捲動視窗規則

- 所有頂層 widget 以 `self._content_frame` 為父節點，**不使用** `self.root`
- 滑鼠滾輪事件綁定在 `_on_mousewheel`，遇到 `Text` widget 直接 return（避免攔截 log 區域）

## Debug Logging 規範

使用 `self.dlog(f"[LABEL-TYPE] 訊息")` 格式：

```
[KTV-CMD]  # FFmpeg 完整指令
[KTV-FF ]  # FFmpeg stderr 每行
[KTV-RC ]  # FFmpeg returncode
[KTV-SUB]  # 字幕路徑與存在狀態
[DL-CMD]   # yt-dlp 完整指令
[SEP-OUT]  # 分離輸出路徑
```

Log 檔位置：`%LOCALAPPDATA%\VocalForge\vocalforge_debug.log`（RotatingFileHandler 5MB × 3）

## yt-dlp 規則

- 所有下載指令必須包含 `--remote-components ejs:github`（解決 YouTube EJS challenge）
- URL 僅接受 `https://` 開頭，禁止 `file://` 或任何其他 scheme

## 注意事項

- **不寫無意義注解**：只在「為什麼這樣做」非顯而易見時才加注解，不解釋 what
- **不加防禦性 fallback**：只在系統邊界（使用者輸入、外部 API）驗證，內部呼叫信任型別
- **不新增 feature**：除非任務明確要求，修 bug 不順手重構周邊代碼
- **CTkButton 顏色**：傳入自訂色彩使用 `or` fallback（`normal_bg = normal_bg or self.ui["primary"]`），避免 override 傳入值

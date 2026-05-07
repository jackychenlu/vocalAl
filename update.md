# 架構重構待辦清單

`vocalforge_ktv_studio.py` 剩餘開發項目。已完成的問題已移除。

---

## 一、架構問題（待修復）

### A. 單一類別承擔太多責任

`VocalForgeStudioApp` 目前仍同時負責 YouTube 下載、FFmpeg 合成、AI 分離、環境部署，全部混在一個類別中。

**拆分進度：**

| 服務 | 狀態 | 說明 |
| --- | --- | --- |
| `services/task_result.py` | ✅ 完成 | TaskResult dataclass |
| `services/task_runner.py` | ✅ 完成 | 統一任務生命週期（start / cancel / finish） |
| `services/ffmpeg_service.py` | ✅ 完成 | Stage 2b |
| `services/download_service.py` | ✅ 完成 | Stage 2c |
| `services/separation_service.py` | ✅ 完成 | Stage 2d |
| `services/environment_service.py` | ✅ 完成 | Stage 2d |
| `VocalForgeStudioApp` 精簡 | ✅ 完成 | Stage 2e（移除已遷移方法） |

### B. 回傳值不統一 ✅ 已修復

所有服務方法現統一回傳 `TaskResult`：

- `FfmpegService.extract_audio / merge_stems / build_ktv_video` → `TaskResult`
- `SeparationService.run_audio_separator` → `TaskResult`（原為 `bool`）
- `DownloadService.pure_download_file / download_youtube` → `TaskResult`

### C. 背景執行緒 UI 安全 ✅ 已修復

- `_start_async_setup` 改用 `runner.start()` 統一管理執行緒
- `EnvironmentService._startup_ort_check` 使用 `self._root.after(0, ...)` 安全更新 UI

### F. 環境部署與 App 類別耦合 ✅ 已修復

所有環境部署邏輯已移至 `EnvironmentService`（約 800 行），App 只持有薄包裝。

### H. FFmpeg 命令分散 ✅ 已修復

所有 FFmpeg 操作集中至 `FfmpegService`；字幕 input index 改為動態計算（`sum(1 for x in cmd if x == "-i")`）。

---

## 二、剩餘開發項目（依執行順序）

### Stage 2b — FfmpegService（優先）

新增 `services/ffmpeg_service.py`，包含：

```python
class FfmpegService:
    def extract_audio(self, video_path, output_mp3) -> TaskResult
    def merge_stems(self, stems: list[str], output) -> TaskResult
    def build_ktv_video(self, video, vocal, instrumental,
                        subtitle, output, fmt, track_mode, ...) -> TaskResult
```

替換上方 Issue H 的 4 處呼叫，字幕 input index 改為 `len(inputs) // 2` 動態計算。

### Stage 2c — DownloadService

新增 `services/download_service.py`，遷移：

- `extract_youtube_video_id`
- `sanitize_filename`
- `_get_ytdlp_command_base`
- `_get_cookie_opts`
- `_get_ytdlp_js_runtime_opts`
- `download_youtube`（MP3/MP4/字幕）
- `pure_download_file`
- `download_youtube_subtitle`

注入依賴：`log_fn`、`progress_fn`、`cancel_event`（由 runner 共享）。

### Stage 2d — SeparationService + EnvironmentService

**SeparationService** (`services/separation_service.py`)：

- `run_audio_separator` → `TaskResult`
- `consolidate_stems`

**EnvironmentService** (`services/environment_service.py`)：

- `check_components`
- `_async_setup_environment` / `_start_async_setup`
- `_startup_ort_check`
- `download_portable_python` / `download_ffmpeg`
- `install_packages_locally` / `_install_ai_stack`
- `_install_ytdlp_silent` / `_check_ytdlp`
- `_is_nvidia_gpu_present` / `_probe_onnxruntime_stack`
- `_build_python_env` / `fix_python_pth`

同步修復 Issue C（messagebox 改 `root.after`，thread 改用 `runner.start()`）。

### Stage 2e — 精簡 VocalForgeStudioApp

移除所有已遷移至 service 層的方法，App 只保留：

- GUI 建構（`setup_ui` 及相關 widget helper）
- 事件入口（`on_start_click`、`start_xxx` 等 4 個方法）
- 日誌與進度更新（`log`、`update_progress`、`update_status`）

---

## 三、長期追蹤（暫不優先）

- 支援命令列參數或拖曳檔案到 EXE
- 將下載、分離、合成流程寫成可測試的純函式（去除 Tkinter 依賴）
- 為 yt-dlp / FFmpeg / audio-separator 建立 dry-run 或 debug log 模式
- 檢查路徑含中文、空白、特殊符號時所有 subprocess 行為
- 檢查所有清理流程，避免失敗時刪掉仍需要的中間檔

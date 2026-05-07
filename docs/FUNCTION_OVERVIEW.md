# VocalForge KTV Studio — 功能與架構說明

版本 `2.11.0`。Windows 桌面 GUI 工具，支援 YouTube 下載、AI 人聲/伴奏分離與 KTV 影片合成。

---

## 一、專案結構

```text
vocalforge_ktv_studio.py   主程式（GUI + 事件入口，~1270 行）
VocalForgeKTVStudio.spec   PyInstaller 打包設定
services/
  task_result.py           TaskResult 資料類別
  task_runner.py           統一任務生命週期（start / cancel / finish）
  ffmpeg_service.py        FFmpeg 操作封裝
  download_service.py      yt-dlp 下載與字幕處理
  separation_service.py    audio-separator 執行與音軌整理
  environment_service.py   可攜式環境部署與 GPU 管理
docs/
  CHANGELOG.md             版本記錄
  FUNCTION_OVERVIEW.md     本文件
  TODO.md                  未實作項目追蹤
```

程式啟動後在程式所在目錄建立或使用下列資料夾：

| 目錄 | 用途 |
|---|---|
| `engine_ffmpeg/` | FFmpeg exe 與 dll |
| `runtime_python/` | 可攜式 Python |
| `ai_libraries/` | CPU AI 套件與共用 Python 套件 |
| `ai_libraries_gpu/` | GPU AI 套件（與 CPU 完全隔離） |
| `ai_models/` | audio-separator AI 模型 |
| `output/` | 預設輸出目錄 |

---

## 二、服務層架構

```text
VocalForgeStudioApp
  │
  ├─ TaskRunner          任務執行緒管理、cancel_event 共享
  ├─ FfmpegService       extract_audio / merge_stems / build_ktv_video
  ├─ DownloadService     download_youtube / pure_download_file / subtitle
  ├─ SeparationService   run_audio_separator / consolidate_stems
  └─ EnvironmentService  check_components / install / gpu_check
```

所有服務方法回傳 `TaskResult(success, path, paths, error)`，App 只負責讀取結果並更新 UI。

服務透過建構子注入依賴（`log_fn`、`progress_fn`、`cancel_event` 等），不直接持有 UI 物件。

---

## 三、GUI 功能分頁

### Tab 1 — YouTube 一鍵轉 KTV

輸入 YouTube 網址，程式依序：

1. yt-dlp 下載 MP4 與可選 CC 字幕。
2. FFmpeg 從 MP4 抽出 MP3。
3. audio-separator AI 分離人聲與伴奏。
4. FFmpeg 合成 KTV 影片（MKV 或 MP4）。

可設定：AI 模型、CPU/GPU、音訊輸出格式、KTV 影片格式、音軌模式（雙音軌 / 左伴唱右人聲）、導唱混合比例、強制 1080p、CC 字幕（僅下載 SRT / 下載並封裝）。

### Tab 2 — YouTube 下載 MP3/MP4

純下載模式，可選下載後順帶執行 AI 音訊分離。

可設定：下載格式（MP3+MP4 / 僅 MP3 / 僅 MP4）、MP4 畫質（最佳 / 1080p / 720p / 480p）、分離格式與模型。

### Tab 3 — 本地影片轉 KTV

加入多個本地影片或整個資料夾，批次：抽音訊 → AI 分離 → 合成 KTV。

支援 `.mp4 .mkv .avi .mov .wmv .webm`。

**字幕導入：** 可手動選擇 `.srt` 套用至所有影片；欄位空白時自動比對與影片同名的 `.srt`（放在影片旁邊即自動套用）。

### Tab 4 — 本地音檔批量分離

加入多個音訊檔，批次執行 AI 人聲/伴奏分離。

支援 `.mp3 .wav .flac .m4a`。

---

## 四、核心流程

### YouTube 一鍵轉 KTV

```text
start_yt_process  → 檢查 URL、GPU 狀態、啟動 runner
yt_process
  dl_svc.download_youtube       yt-dlp 下載 MP4 + 字幕
  ffmpeg_svc.extract_audio      MP4 → MP3
  sep_svc.run_audio_separator   AI 分離
  sep_svc.consolidate_stems     整理 vocals / instrumental
  ffmpeg_svc.build_ktv_video    合成 MKV/MP4
finish_processing               還原 UI（TaskRunner 自動呼叫）
```

### YouTube 純下載

```text
start_pure_download → 檢查 URL、啟動 runner
pure_download_process
  dl_svc.extract_youtube_video_id
  dl_svc.pure_download_file     yt-dlp 下載指定格式
  sep_svc.run_audio_separator   （可選）AI 分離
```

### 本地影片轉 KTV

```text
start_local_v_process → 啟動 runner
local_v_batch_process（逐一處理）
  ffmpeg_svc.extract_audio      影片 → 暫存 MP3
  sep_svc.run_audio_separator   AI 分離
  sep_svc.consolidate_stems     整理音軌
  ffmpeg_svc.build_ktv_video    合成 KTV（套用字幕）
```

### 本地音檔批量分離

```text
start_separation → 檢查清單與 GPU 狀態、啟動 runner
batch_process（逐一處理）
  sep_svc.run_audio_separator
```

---

## 五、服務說明

### FfmpegService（`services/ffmpeg_service.py`）

| 方法 | 說明 |
|---|---|
| `extract_audio(video, output_mp3)` | FFmpeg 從影片抽出 320k MP3 |
| `merge_stems(stems, output)` | 用 `amix` 合併多個音軌（Demucs 多音軌用） |
| `build_ktv_video(...)` | 合成 KTV 影片，支援雙音軌、左伴右人聲、SRT/mov_text 字幕、強制 1080p |

字幕 input index 動態計算（`sum(1 for x in cmd if x == "-i")`），不受輸入數量影響。

### DownloadService（`services/download_service.py`）

| 方法 | 說明 |
|---|---|
| `download_youtube(url, output_dir, mode, download_subtitles)` | 下載 MP4/MP3，可同時抓字幕 |
| `pure_download_file(url, output_dir, file_type, quality, video_id)` | 純下載單一格式 |
| `download_youtube_subtitle(url, output_dir, video_id)` | 下載 CC 字幕，語言優先繁中→簡中→英文→日文 |
| `align_subtitle_filename(subtitle_file, target_media_file)` | 字幕改名為與影片同主檔名 |
| `extract_youtube_video_id(url)` | 從 URL 擷取 video ID |
| `sanitize_filename(title, max_len)` | 清除非法字元，限制 UTF-8 byte 長度 |

cookie 與 JS runtime 選項透過 `cookie_browser_fn` callback 讀取，不直接依賴 UI。

### SeparationService（`services/separation_service.py`）

| 方法 | 說明 |
|---|---|
| `run_audio_separator(input_file, output_dir, fmt, device_str, model, overlap, denoise)` | 執行 audio-separator CLI，回傳 `TaskResult` |
| `consolidate_stems(input_audio, reference_video, output_dir, fmt)` | 整理輸出音軌，合併 Demucs 多音軌，清理暫存檔 |

MDX 模型失敗時自動回退 `htdemucs.yaml` 重試一次。GPU 核心不相容時提示使用者切換 CPU 或執行一鍵修復。

### EnvironmentService（`services/environment_service.py`）

| 方法 | 說明 |
|---|---|
| `check_components(prompt)` | 檢查 Python/FFmpeg/PyTorch/audio-separator/ONNX/yt-dlp，缺少時詢問安裝 |
| `async_setup_environment(install_mode)` | 執行完整環境部署（由 TaskRunner 管理執行緒） |
| `ensure_runtime_stack_ready(device)` | 分離前確認 AI stack 可用，GPU 不可用時自動回退 CPU |
| `build_python_env(lib_dir, include_gpu_runtime)` | 建立子程序用的隔離環境變數（PYTHONPATH、PATH） |
| `install_packages_locally(install_mode)` | 安裝 pip 與 AI 套件（CPU / GPU / both / auto） |
| `download_portable_python()` | 下載 Python embed zip，驗證後解壓 |
| `download_ffmpeg()` | 從 BtbN 或 gyan.dev 下載 FFmpeg |
| `fix_python_pth()` | 修正 embed Python 的 `._pth` 以讓 import 正常 |

pip 安裝進入「Installing collected packages」沉默階段時，heartbeat thread 每 20 秒印一行提示，避免使用者誤判卡死。

---

## 六、AI 模型

| 模型 | 類型 | 特性 |
|---|---|---|
| `UVR-MDX-NET-Inst_HQ_3.onnx` | MDX | 伴奏優化 |
| `UVR-MDX-NET-Inst_HQ_4.onnx` | MDX | 高品質綜合 |
| `Kim_Vocal_2.onnx` | MDX | 極致人聲提取 |
| `htdemucs.yaml` | Demucs | 4 音軌高品質分離 |
| `htdemucs_ft.yaml` | Demucs | 流行樂優化 |
| `htdemucs_6s.yaml` | Demucs | 6 音軌（含 Guitar/Piano） |

Demucs 模型輸出 Bass/Drums/Other 等多音軌，`consolidate_stems` 會用 FFmpeg `amix` 合併成一個伴奏檔。

---

## 七、輸出檔案

| 類型 | 命名規則 |
|---|---|
| YouTube 影片 | `<安全標題>_<video_id>.mp4` |
| 人聲 | `<影片名>_vocals.<fmt>` |
| 伴奏 | `<影片名>_instrumental.<fmt>` |
| KTV 影片 | `<影片名>_KTV.<mkv\|mp4>` |
| 字幕 | `<影片名>.srt` |

音訊分離格式可選 MP3 / WAV / FLAC。

---

## 八、狀態管理與取消

| 物件 | 位置 | 用途 |
|---|---|---|
| `is_processing` | App | 防止重複啟動任務 |
| `cancel_event` | TaskRunner（共享） | 跨執行緒取消信號，所有服務輪詢此事件 |
| `dl_svc.last_downloaded_subtitle` | DownloadService | 最近下載的字幕路徑 |

`TaskRunner.start()` 啟動背景執行緒，任務結束後自動呼叫 `finish_processing()` 還原 UI。`cancel()` 設定 `cancel_event`，服務層在迴圈中偵測到後終止子程序並回傳。

---

## 九、主要外部命令

```text
# FFmpeg 抽音訊
ffmpeg -y -i <video> -vn -acodec libmp3lame -ab 320k <audio.mp3>

# FFmpeg 合成雙音軌 KTV
ffmpeg -i <video> -i <vocals> -i <instrumental>
  -filter_complex "[1:a][2:a]amix=inputs=2:duration=first:weights='V I'[mix]"
  -map 0:v -map [mix] -map 2:a -c:a aac -b:a 320k [-c:v copy | -c:v libx264] <output>

# FFmpeg 合成左伴唱右人聲
ffmpeg -i <video> -i <instrumental> -i <vocals>
  -filter_complex "[1:a]pan=mono|c0=c0[inst];[2:a]pan=mono|c0=c0[voc];[inst][voc]amerge=inputs=2[lr]"
  -map 0:v -map [lr] -ac 2 -c:a aac -b:a 320k <output>

# yt-dlp 下載 MP4
yt-dlp --no-playlist --ffmpeg-location engine_ffmpeg -f <format> --merge-output-format mp4 -o <out> <url>

# yt-dlp 下載 MP3
yt-dlp --no-playlist --ffmpeg-location engine_ffmpeg -x --audio-format mp3 --audio-quality 320K -o <out> <url>
```

---

## 十、維護注意事項

1. **Windows 專屬**：使用 `ffmpeg.exe`、`CREATE_NO_WINDOW`、`os.startfile`、`wmic`、PowerShell。

2. **CPU / GPU 套件必須隔離**：`ai_libraries` 與 `ai_libraries_gpu` 不應混用。`build_python_env` 在執行時動態注入，不寫死到 `._pth`。

3. **audio-separator CLI 參數**：MDX 用 `--mdx_*` 前綴，Demucs 用 `--demucs_*`。升級 audio-separator 版本時需確認參數相容性。

4. **yt-dlp 隨 YouTube 變動**：格式清單、metadata 欄位、字幕 API 可能需要隨新版 yt-dlp 調整。

5. **GUI 執行緒安全**：所有 UI 更新需透過 `root.after()`。`log`、`update_progress`、`update_status` 已包裝。EnvironmentService 透過 `_root.after()` 更新裝置設定。

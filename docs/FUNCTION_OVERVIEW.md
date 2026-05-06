# VocalForge KTV Studio 功能說明

本文整理 `vocalforge_ktv_studio.py` 的主要功能、執行流程、外部依賴與各函式職責。此程式是一個 Windows 桌面 GUI 工具，透過 Tkinter 提供 YouTube 下載、AI 人聲/伴奏分離，以及 KTV 影片合成功能。

## 一、程式定位

`vocalforge_ktv_studio.py` 是 VocalForge KTV Studio 的主程式，目前版本為 `2.10.1`。

主要用途：

- 下載 YouTube 影片或音訊。
- 將 YouTube 影片自動轉成 KTV 伴唱影片。
- 對本地音檔批次做人聲/伴奏分離。
- 對本地影片抽出音訊、分離人聲與伴奏，再合成 KTV 影片。
- 自動下載與部署可攜式 Python、FFmpeg、PyTorch、ONNX Runtime、audio-separator、yt-dlp 等依賴。
- 支援 CPU 與 NVIDIA GPU 兩套 AI 執行環境，並盡量避免 CPU/GPU 套件互相污染。

## 二、主要技術組成

| 類別 | 使用內容 | 用途 |
|---|---|---|
| GUI | `tkinter`, `ttk`, `scrolledtext` | 建立桌面操作介面、分頁、按鈕、進度條與日誌 |
| 執行緒 | `threading` | 避免下載、分離、合成等耗時任務卡住 UI |
| 子程序 | `subprocess` | 執行 FFmpeg、yt-dlp、內建 Python、pip、nvidia-smi 等命令 |
| 路徑處理 | `pathlib.Path`, `os`, `shutil` | 管理外部工具、輸出目錄、暫存檔、模型資料夾 |
| 下載與解壓 | `urllib.request`, `zipfile`, `ssl` | 下載可攜式 Python、FFmpeg、get-pip.py |
| 影音處理 | FFmpeg | 抽音訊、合併音軌、封裝 MKV/MP4、字幕轉封裝 |
| YouTube | yt-dlp | 下載影片、音訊、字幕、讀取影片 metadata |
| AI 分離 | audio-separator, PyTorch, ONNX Runtime | 執行 MDX / Demucs 模型做人聲與伴奏分離 |

## 三、目錄結構

程式啟動後會在程式所在目錄建立或使用下列資料夾：

| 目錄 | 用途 |
|---|---|
| `engine_ffmpeg` | 存放 FFmpeg 的 exe 與 dll |
| `runtime_python` | 存放可攜式 Python |
| `ai_libraries` | CPU 版 AI 套件與共用 Python 套件 |
| `ai_libraries_gpu` | GPU 版 AI 套件，和 CPU 套件分開存放 |
| `ai_models` | 存放 audio-separator 使用的 AI 模型 |
| `output` | 預設輸出資料夾 |

程式也會自動遷移舊版資料夾名稱：

| 舊名稱 | 新名稱 |
|---|---|
| `bin` | `engine_ffmpeg` |
| `python_env` | `runtime_python` |
| `packages` | `ai_libraries` |
| `packages_gpu` | `ai_libraries_gpu` |
| `models` | `ai_models` |

## 四、GUI 功能分頁

### 1. YouTube 一鍵轉 KTV

輸入 YouTube 網址後，程式會：

1. 使用 yt-dlp 下載 MP4 影片。
2. 視設定下載 YouTube CC 字幕。
3. 使用 FFmpeg 從影片抽出 MP3 音訊。
4. 使用 audio-separator 分離人聲與伴奏。
5. 整理分離後的音軌檔名。
6. 使用 FFmpeg 合成 KTV 影片，輸出 MKV 或 MP4。

可設定：

- AI 模型。
- CPU / GPU。
- 輸出格式。
- KTV 影片格式：MKV / MP4。
- 音軌模式：雙音軌或左伴唱右人聲。
- 導唱混合比例。
- 強制輸出 1080p。
- YouTube CC 字幕下載與封裝。

### 2. YouTube 下載 MP3/MP4

此分頁只做下載，也可以選擇下載後順便做 AI 音訊分離。

可設定：

- 下載 MP3 + MP4、僅 MP3、僅 MP4。
- MP4 畫質：最佳、1080p、720p、480p。
- 下載後是否分離人聲/伴奏。
- 分離輸出格式：MP3 / WAV / FLAC。
- 分離模型。

### 3. 本地影片轉 KTV

可加入多個本地影片或整個資料夾，程式會批次處理：

1. 用 FFmpeg 從影片抽出 MP3。
2. 用 audio-separator 分離人聲與伴奏。
3. 整理輸出音軌。
4. 合成 KTV MKV/MP4。

支援副檔名：

- `.mp4`
- `.mkv`
- `.avi`
- `.mov`
- `.wmv`
- `.webm`

### 4. 本地音檔批量分離

可加入多個音訊檔，批次做人聲與伴奏分離。

支援副檔名：

- `.mp3`
- `.wav`
- `.flac`
- `.m4a`

## 五、核心流程

### YouTube 轉 KTV 流程

對應主要函式：

- `start_yt_process`
- `yt_process`
- `download_youtube`
- `download_youtube_subtitle`
- `run_audio_separator`
- `consolidate_stems`
- `synthesize_mkv`
- `finish_processing`

流程：

```text
使用者輸入 YouTube 網址
        |
        v
start_yt_process 檢查 URL、GPU 狀態、鎖定 UI
        |
        v
yt_process 建立輸出目錄並決定字幕模式
        |
        v
download_youtube 使用 yt-dlp 下載 MP4 與可選字幕
        |
        v
FFmpeg 從 MP4 擷取 MP3 音訊
        |
        v
run_audio_separator 執行 AI 分離
        |
        v
consolidate_stems 整理 vocals / instrumental
        |
        v
synthesize_mkv 合成 KTV MKV 或 MP4
        |
        v
finish_processing 還原 UI 狀態
```

### 純 YouTube 下載流程

對應主要函式：

- `start_pure_download`
- `pure_download_process`
- `pure_download_file`
- `run_audio_separator`

流程：

```text
使用者輸入 YouTube 網址
        |
        v
start_pure_download 檢查 URL 並啟動背景執行緒
        |
        v
pure_download_process 根據格式設定下載 MP3 / MP4
        |
        v
pure_download_file 使用 yt-dlp 下載指定格式
        |
        v
若勾選音訊分離，對下載後 MP3 呼叫 run_audio_separator
```

### 本地影片轉 KTV 流程

對應主要函式：

- `start_local_v_process`
- `local_v_batch_process`
- `run_audio_separator`
- `consolidate_stems`
- `synthesize_mkv`

流程：

```text
加入本地影片清單
        |
        v
start_local_v_process 鎖定 UI 並啟動批次執行緒
        |
        v
local_v_batch_process 逐一處理影片
        |
        v
FFmpeg 擷取暫存 MP3
        |
        v
run_audio_separator 分離人聲與伴奏
        |
        v
consolidate_stems 整理輸出音軌
        |
        v
synthesize_mkv 合成 KTV 影片
```

### 本地音檔批量分離流程

對應主要函式：

- `start_separation`
- `batch_process`
- `run_audio_separator`

流程：

```text
加入音訊檔清單
        |
        v
start_separation 檢查清單與 GPU 狀態
        |
        v
batch_process 逐一處理音檔
        |
        v
run_audio_separator 執行 audio-separator
```

## 六、重要函式說明

### `__init__(self, root)`

初始化整個應用程式。

主要工作：

- 設定版本與視窗標題。
- 判斷目前是 Python 腳本執行或 PyInstaller EXE 執行。
- 建立外部依賴目錄。
- 遷移舊資料夾名稱。
- 設定 `PATH`、`PYTHONPATH` 與 DLL 搜尋路徑。
- 初始化狀態變數。
- 建立 UI。
- 啟動後延遲檢查外部組件。

### `setup_ui(self)`

建立完整 GUI。

包含：

- 四個功能分頁。
- 輸出目錄設定。
- YouTube Cookie 選項。
- CPU/GPU 選項。
- AI 模型選擇。
- 音訊輸出格式。
- KTV 影片輸出格式。
- 音軌模式。
- 字幕模式。
- 開始、取消、初始化按鈕。
- 進度條與日誌區。

### `_get_cookie_opts(self)`

根據使用者選擇的瀏覽器產生 yt-dlp cookie 參數。

若選擇 Chrome，會回傳：

```text
--cookies-from-browser chrome
```

用途是避免 YouTube 429、登入限制或年齡限制等下載問題。

### `_get_ytdlp_js_runtime_opts(self)`

偵測本機是否有 JavaScript runtime：

- Deno
- Node.js
- Bun
- QuickJS

若找到，會傳給 yt-dlp：

```text
--js-runtimes <runtime>:<path>
```

用途是提升 YouTube 影片資訊、格式清單與字幕擷取成功率。

### `_get_ytdlp_command_base(self)`

決定 yt-dlp 的啟動方式，優先順序：

1. `runtime_python/Scripts/yt-dlp.exe`
2. `ai_libraries/yt_dlp/__main__.py`
3. `python.exe -m yt_dlp`

這樣可以兼容 embed Python 或 `--target` 安裝的 yt-dlp。

### `on_tab_changed(self, event)`

依目前分頁切換 UI 顯示。

例如：

- 純下載分頁隱藏 AI/KTV 設定。
- YouTube KTV 分頁顯示字幕選項。
- 本地影片 KTV 分頁顯示影片輸出選項，但關閉 YouTube 字幕選項。

### `on_start_click(self)`

主開始按鈕的分派器。

依目前分頁呼叫：

| 分頁 | 呼叫函式 |
|---|---|
| YouTube 一鍵轉 KTV | `start_yt_process` |
| YouTube 下載 | `start_pure_download` |
| 本地影片轉 KTV | `start_local_v_process` |
| 本地音檔批量分離 | `start_separation` |

### `start_yt_process(self)`

啟動 YouTube 轉 KTV 任務。

主要檢查：

- URL 是否為空。
- 是否已有任務在執行。
- 若選擇 GPU，檢查 GPU 環境是否可用。

通過後會：

- 設定 `is_processing = True`。
- 啟用取消按鈕。
- 清空日誌。
- 啟動背景執行緒執行 `yt_process`。

### `yt_process(self, url)`

YouTube 轉 KTV 的主流程。

主要步驟：

1. 建立輸出目錄。
2. 根據 UI 設定決定字幕模式。
3. 呼叫 `download_youtube` 下載影片與可選字幕。
4. 用 FFmpeg 從影片抽出 MP3。
5. 呼叫 `run_audio_separator` 做人聲/伴奏分離。
6. 呼叫 `consolidate_stems` 整理音軌檔名。
7. 呼叫 `synthesize_mkv` 合成 MKV/MP4。
8. 顯示完成訊息並開啟輸出資料夾。

### `download_youtube(self, url, output_dir, mode="both", download_subtitles=False)`

使用 yt-dlp 下載 YouTube 內容。

支援模式：

- `both`：下載 MP4 與 MP3。
- `mp4`：只下載 MP4。
- `mp3`：只下載 MP3。

重要特性：

- 使用影片 ID 作為可靠追蹤標記。
- 嘗試讀取影片標題並轉成安全檔名。
- MP4 優先下載 1080p 以內的 mp4 + m4a。
- 若主要格式失敗，會改用相容模式重試。
- MP3 使用 `-x --audio-format mp3 --audio-quality 320K`。
- 可搭配 cookie 與 JS runtime。
- 可在下載影片時先抓字幕。

### `download_youtube_subtitle(self, url, output_dir, video_id)`

下載 YouTube CC 字幕。

主要策略：

1. 使用 yt-dlp 讀取影片 metadata。
2. 從手動字幕與自動字幕中挑選語言。
3. 優先順序大致為繁中、簡中、中文、英文、日文。
4. 逐一嘗試下載。
5. 轉成 `.srt`。
6. 找到可用字幕後回傳字幕檔路徑。

若無字幕或下載失敗，會回傳 `None`，不會中斷主影片處理。

### `align_subtitle_filename(self, subtitle_file, target_media_file)`

將字幕檔改名成與目標影片同主檔名。

例如：

```text
原字幕：abc123.zh-TW.srt
目標影片：MySong_KTV.mkv
輸出字幕：MySong_KTV.srt
```

用途是讓播放器更容易自動載入外掛字幕，也讓封裝後的字幕檔名一致。

### `run_audio_separator(self, input_file, output_dir)`

音訊分離核心函式。

主要步驟：

1. 修正可攜式 Python 的 `._pth` 設定。
2. 根據 UI 選擇 CPU 或 GPU。
3. 呼叫 `_ensure_runtime_stack_ready` 確認 AI 執行環境可用。
4. 建立隔離 Python 環境變數。
5. 使用內建 Python 執行 `audio_separator.utils.cli.main()`。
6. 根據模型類型加入不同參數：
   - MDX 模型：`--mdx_overlap`、`--mdx_segment_size`、`--mdx_hop_length`、可選去噪。
   - Demucs 模型：`--demucs_segment_size`、`--demucs_shifts`、`--demucs_overlap`。
7. GPU 模式會加入 `--use_autocast`。
8. 監看輸出日誌、取消事件與錯誤。
9. 檢查輸出目錄是否真的產生人聲/伴奏檔。

錯誤處理：

- 若 GPU 回報 `no kernel image is available`，提示可能是 GPU 太新或核心不相容。
- 若 MDX 模型不支援，會自動回退到 `htdemucs.yaml` 重試一次。
- 若程式回報成功但找不到輸出檔，視為失敗。

### `consolidate_stems(self, input_audio, reference_video, output_dir)`

整理 audio-separator 的輸出音軌。

主要工作：

- 將 `(Vocals)` 重新命名為：

```text
<影片安全檔名>_vocals.<fmt>
```

- 將 `(Instrumental)` 或 `(No Vocals)` 重新命名為：

```text
<影片安全檔名>_instrumental.<fmt>
```

- 若使用 Demucs 多音軌模型，可能會產生 Bass、Drums、Other、Guitar、Piano 等音軌。此函式會使用 FFmpeg 將非人聲音軌混合成一個伴奏檔。
- 清理原始暫存音檔與分離後的暫存 stem 檔。

回傳：

```python
(vocals_file_path, instrumental_file_path)
```

若找不到必要檔案，對應項目會是 `None`。

### `synthesize_mkv(self, video_file, vocal_file, instrumental_file, output_file, subtitle_file=None)`

合成 KTV 影片。

支援兩種音軌模式：

#### 雙音軌模式

輸出：

- 音軌 1：導唱，人聲 + 伴奏，比例由「導唱混合比例」控制。
- 音軌 2：純伴奏。

FFmpeg 會使用 `amix` 混合人聲與伴奏。

#### 左伴唱 / 右人聲模式

輸出：

- 單一立體聲音軌。
- 左聲道為伴奏。
- 右聲道為人聲。

FFmpeg 會使用 `pan` 與 `amerge` 組成立體聲。

其他功能：

- 音訊轉 AAC，位元率 320k。
- 可封裝 SRT 字幕。
- MKV 使用 SRT 字幕。
- MP4 使用 `mov_text` 字幕。
- 可選擇直接複製原始影片串流，或強制轉成 1080p H.264。
- MP4 會加入 `+faststart`。

### `start_pure_download(self)` / `pure_download_process(self, url)` / `pure_download_file(...)`

這組函式負責 YouTube 純下載。

`pure_download_file` 可下載：

- MP3：抽音訊並轉 320K MP3。
- MP4：依畫質設定下載影片與音訊，並合併成 MP4。

下載前後會比較輸出目錄中的檔案清單，確認是否真的產出新檔案。

若使用者勾選下載後音訊分離，`pure_download_process` 會找最新下載的 MP3，暫時套用下載分頁的模型與輸出格式，再呼叫 `run_audio_separator`。

### `start_local_v_process(self)` / `local_v_batch_process(self)`

負責本地影片批次轉 KTV。

每支影片會：

1. 抽出暫存 MP3。
2. 執行 AI 音訊分離。
3. 整理 vocals / instrumental。
4. 合成 KTV MKV/MP4。
5. 更新進度與日誌。

### `start_separation(self)` / `batch_process(self)`

負責本地音檔批量分離。

`start_separation` 會先檢查音檔清單與 GPU 狀態。

`batch_process` 逐一呼叫 `run_audio_separator`，並在完成後開啟輸出資料夾。

## 七、環境初始化與修復

### `check_components(self, prompt=True)`

檢查必要組件：

- 可攜式 Python。
- FFmpeg。
- PyTorch。
- audio-separator。
- ONNX Runtime。
- yt-dlp。

若缺少組件，會詢問使用者要安裝：

- 自動偵測。
- CPU + GPU 雙支援。
- 僅 GPU。
- 僅 CPU。

### `_async_setup_environment(self, install_mode="auto")`

實際執行環境部署。

流程：

1. 建立必要資料夾。
2. 下載可攜式 Python。
3. 下載 FFmpeg。
4. 檢查 AI 套件是否完整。
5. 依模式安裝或修復 CPU/GPU AI 套件。
6. 安裝 yt-dlp。
7. 更新 UI 狀態。

### `download_portable_python(self)`

下載 Python embed zip。

特點：

- 有多個 Python 官方備用來源。
- 下載前檢查目錄是否可寫。
- 驗證 zip 是否有效。
- 解壓後呼叫 `fix_python_pth`。

### `download_ffmpeg(self)`

下載 FFmpeg。

來源：

1. BtbN GitHub Release。
2. gyan.dev essentials build。

只抽出 zip 裡 `bin` 目錄下的 `.exe` 與 `.dll`。

### `install_packages_locally(self, install_mode="auto")`

安裝 pip 與 AI 依賴。

支援模式：

- `cpu`
- `gpu`
- `both`
- `auto`

會偵測：

- 是否有 NVIDIA GPU。
- 是否為 RTX 50 系列。
- 路徑是否含中文或特殊字元。

### `_install_ai_stack(self, target_dir, target_mode="cpu", is_rtx50=False)`

安裝指定 AI stack。

CPU 模式安裝：

- `torch==2.5.1+cpu`
- `torchvision==0.20.1+cpu`
- `torchaudio==2.5.1+cpu`
- `onnxruntime`
- `audio-separator`

GPU 模式一般安裝：

- CUDA 12.4 版 PyTorch。
- NVIDIA CUDA / cuDNN / cuBLAS 相關 pip 套件。
- `onnxruntime-gpu`
- `audio-separator[gpu]`

RTX 50 系列安裝：

- CUDA 12.8 版 PyTorch。

### `_build_python_env(self, lib_dir, include_gpu_runtime=False)`

建立子程序用的 Python 環境變數。

重點：

- 移除 `PYTHONHOME`。
- 設定 `PYTHONPATH` 到指定 AI 套件目錄。
- 設定 `PYTHONNOUSERSITE=1`，避免讀到使用者全域套件。
- 將 Python、FFmpeg、ONNX Runtime、GPU DLL 相關路徑放入 `PATH`。

這是 CPU/GPU 隔離能成立的關鍵。

### `_probe_onnxruntime_stack(self, lib_dir, expect_gpu=False)`

用內建 Python 快速測試指定目錄的 ONNX Runtime 是否可載入。

可能回傳：

- `ORT_OK_CPU`
- `ORT_OK_GPU`
- `ORT_NO_CUDA`
- `ORT_DLL_FAIL`
- `ORT_ERR`
- `STACK_MISSING`
- `ORT_TIMEOUT`

### `_ensure_runtime_stack_ready(self, device)`

執行音訊分離前的保護檢查。

功能：

- GPU 可用則使用 GPU。
- GPU 不可用時自動回退 CPU。
- CPU 核心缺失或損壞時，嘗試自動修復 CPU 核心。
- 修復失敗才停止任務。

### `check_gpu_env(self)`

深度檢測 GPU 環境。

檢查項目：

- 是否有 NVIDIA GPU。
- ONNX Runtime 版本與 providers。
- CUDAExecutionProvider 是否存在。
- PyTorch 版本。
- `torch.cuda.is_available()` 是否可用。
- 是否能實際建立 CUDA tensor。
- 是否遇到 RTX 50 / `sm_120` 不相容。

若環境不完整，會提示使用者執行一鍵修復。

### `_is_nvidia_gpu_present(self)`

檢查系統是否有啟用的 NVIDIA 顯示卡。

優先使用：

```text
wmic path win32_VideoController get Name
```

備援使用 PowerShell：

```text
Get-PnpDevice -Class Display
```

## 八、字幕處理

程式支援 YouTube CC 字幕：

- 可只下載 `.srt`。
- 可下載後封裝進 MKV/MP4。
- MKV 字幕 codec 使用 `srt`。
- MP4 字幕 codec 使用 `mov_text`。
- 字幕語言優先中文，其次英文與日文。
- 字幕檔會盡量改名為與影片同主檔名。

相關函式：

- `refresh_yt_subtitle_mode_ui`
- `download_youtube_subtitle`
- `normalize_subtitle_filename`
- `align_subtitle_filename`
- `synthesize_mkv`

## 九、取消與狀態管理

主要狀態變數：

| 變數 | 用途 |
|---|---|
| `is_processing` | 是否正在執行任務 |
| `cancel_event` | 跨執行緒取消信號 |
| `_current_process` | 目前正在執行的子程序 |
| `_last_downloaded_subtitle` | 最近一次下載的字幕檔 |

`cancel_processing` 會：

- 設定取消事件。
- 嘗試 terminate 目前子程序。
- 停用取消按鈕。
- 在日誌記錄取消訊息。

`finish_processing` 會：

- 還原 `is_processing`。
- 清除取消事件。
- 清空目前子程序參考。
- 重新啟用開始按鈕。
- 停用取消按鈕。
- 將狀態改成準備就緒。

## 十、檔名與路徑安全

### `sanitize_filename(title, max_len=80)`

清理影片標題成安全檔名。

處理內容：

- 移除 Windows 非法字元：`\/:*?"<>|`。
- 移除控制字元。
- 合併空白與底線。
- 限制字元長度。
- 限制 UTF-8 byte 長度，避免 Windows 單檔名過長。
- 若清理後為空，使用 `video`。

這能降低 yt-dlp、FFmpeg、Windows 檔案系統在特殊標題上失敗的機率。

## 十一、模型支援

UI 提供的模型包含：

- `UVR-MDX-NET-Inst_HQ_3.onnx`
- `UVR-MDX-NET-Inst_HQ_4.onnx`
- `Kim_Vocal_2.onnx`
- `htdemucs.yaml`
- `htdemucs_ft.yaml`
- `htdemucs_6s.yaml`

模型類型：

| 類型 | 判斷方式 | 特性 |
|---|---|---|
| MDX | 檔名非 `.yaml` | 通常輸出 Vocals / Instrumental 或 No Vocals |
| Demucs | 檔名為 `.yaml` | 可能輸出 Bass / Drums / Other / Guitar / Piano 等多音軌 |

若 MDX 模型因 hash 或參數不支援而失敗，程式會自動改用 `htdemucs.yaml` 重試一次。

## 十二、輸出檔案

依不同流程可能產生：

| 類型 | 範例 |
|---|---|
| YouTube 影片 | `<安全標題>_<video_id>.mp4` |
| YouTube 音訊 | `<安全標題>_<video_id>_audio.mp3` |
| 人聲 | `<影片名>_vocals.mp3` |
| 伴奏 | `<影片名>_instrumental.mp3` |
| KTV 影片 | `<影片名>_KTV.mkv` 或 `<影片名>_KTV.mp4` |
| 字幕 | `<影片名>.srt` |

輸出音訊格式可選：

- MP3
- WAV
- FLAC

## 十三、主要外部命令

### yt-dlp 下載 MP4

大致形式：

```text
yt-dlp --no-playlist --ffmpeg-location engine_ffmpeg -f <format> --merge-output-format mp4 -o <output> <url>
```

### yt-dlp 下載 MP3

大致形式：

```text
yt-dlp --no-playlist --ffmpeg-location engine_ffmpeg -x --audio-format mp3 --audio-quality 320K -o <output> <url>
```

### FFmpeg 抽音訊

```text
ffmpeg -y -i <video> -vn -acodec libmp3lame -ab 320k <audio.mp3>
```

### FFmpeg 合成雙音軌 KTV

概念：

```text
ffmpeg -i <video> -i <vocals> -i <instrumental>
  -filter_complex "[1:a][2:a]amix=inputs=2:duration=first:weights='0.50 0.50'[mix]"
  -map 0:v -map [mix] -map 2:a
  -c:a aac -b:a 320k
  -c:v copy
  <output.mkv>
```

### FFmpeg 合成左伴唱右人聲

概念：

```text
ffmpeg -i <video> -i <instrumental> -i <vocals>
  -filter_complex "[1:a]pan=mono|c0=c0[inst_mono];[2:a]pan=mono|c0=c0[voc_mono];[inst_mono][voc_mono]amerge=inputs=2[lr]"
  -map 0:v -map [lr]
  -ac 2 -c:a aac -b:a 320k
  <output.mkv>
```

## 十四、錯誤處理重點

程式針對常見問題做了處理：

- YouTube 429 或登入限制：提供 browser cookie 選項。
- YouTube 字幕/格式清單不完整：嘗試使用 JS runtime。
- FFmpeg 缺失：可自動下載。
- Python 缺失：可自動下載 embed Python。
- AI 套件缺失或損壞：可一鍵修復。
- GPU 不可用：自動回退 CPU。
- RTX 50 系列 PyTorch 不相容：嘗試安裝 cu128 版本。
- 模型不支援：回退 Demucs。
- 檔名特殊字元：使用 `sanitize_filename`。
- 路徑含中文或特殊字元：會提示可能導致安裝失敗。
- 下載程序回報成功但找不到檔案：會額外檢查輸出目錄。

## 十五、維護注意事項

1. 程式目前高度依賴 Windows 環境。
   - 使用 `ffmpeg.exe`、`python.exe`、`CREATE_NO_WINDOW`、`os.startfile`、`wmic`、PowerShell。

2. CPU 與 GPU 套件必須保持隔離。
   - `ai_libraries` 與 `ai_libraries_gpu` 不應混用。
   - `fix_python_pth` 刻意不把 AI 套件固定寫死到 `._pth`，而是在執行時動態注入。

3. `run_audio_separator` 是最核心且風險最高的函式。
   - 它負責模型參數、CPU/GPU 環境、子程序、錯誤判斷、輸出驗證。
   - 修改前應先確認 audio-separator CLI 參數是否仍相容。

4. `download_youtube` 與 `download_youtube_subtitle` 需隨 yt-dlp 變動維護。
   - YouTube 網站變化頻繁，yt-dlp 參數與行為可能需要更新。

5. `synthesize_mkv` 的 FFmpeg map/index 要小心。
   - 字幕輸入目前固定假設在第 3 個 input index。
   - 若未來增加更多輸入，必須同步調整 `subtitle_input_index`。

6. GUI 更新必須使用 `root.after`。
   - 背景執行緒不可直接大量操作 Tkinter widget。
   - 現有 `log`、`update_progress`、`update_status` 已透過 `root.after` 包裝。

7. 目前程式是單檔大型類別。
   - 若後續要重構，建議拆分為：
     - GUI 層。
     - YouTube 下載服務。
     - AI 分離服務。
     - FFmpeg 合成服務。
     - 環境部署服務。
     - 日誌/狀態管理。

## 十六、總結

此 Python 檔是一個完整的可攜式 KTV 製作工具。它不只是 GUI，而是把環境部署、YouTube 下載、字幕處理、音訊分離、GPU/CPU 相容性、FFmpeg 合成與批次任務都包在同一個 `VocalForgeStudioApp` 類別中。

核心價值在於：

- 使用者不用手動安裝複雜 AI 環境。
- 支援 YouTube 與本地檔案兩種來源。
- 支援 CPU 與 NVIDIA GPU。
- 支援 MDX 與 Demucs 分離模型。
- 可直接產出適合 KTV 使用的 MKV/MP4 影片。

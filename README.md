# VocalForge KTV Studio

VocalForge KTV Studio 是一套 Windows 桌面工具，用於 YouTube 下載、AI 人聲/伴奏分離，以及 KTV 伴唱影片製作。

## 核心功能

- YouTube 一鍵製作 KTV 影片
- YouTube MP3 / MP4 純下載
- 本地音檔批次分離人聲與伴奏
- 本地影片批次轉 KTV 影片（支援自動或手動帶入 SRT 字幕）
- 支援 MKV / MP4 輸出
- 支援 YouTube CC 字幕下載與封裝
- 支援 CPU 與 NVIDIA GPU 運算環境
- 內建環境初始化與一鍵修復流程

## 專案結構

```text
vocalforge_ktv_studio.py      主程式與 Tkinter GUI
VocalForgeKTVStudio.spec      PyInstaller 打包設定
services/
  task_result.py              TaskResult 資料類別
  task_runner.py              任務執行緒生命週期管理
  ffmpeg_service.py           FFmpeg 操作封裝
  download_service.py         yt-dlp 下載與字幕處理
  separation_service.py       audio-separator 執行與音軌整理
  environment_service.py      可攜式環境部署與 GPU 管理
docs/
  CHANGELOG.md                版本記錄
  FUNCTION_OVERVIEW.md        功能與架構說明
  TODO.md                     未實作項目追蹤
```

## 開發環境

- Windows 10 / 11
- Python 3.10+ 或 `py` launcher
- PyInstaller

安裝打包工具：

```powershell
py -m pip install --upgrade pyinstaller
```

## 執行

```powershell
py vocalforge_ktv_studio.py
```

## 打包

使用 spec 檔打包（保留 hiddenimports 與路徑設定）：

```powershell
py -m PyInstaller --clean VocalForgeKTVStudio.spec
```

輸出檔：

```text
dist/VocalForgeKTVStudio.exe
```

## 維護文件

- [docs/FUNCTION_OVERVIEW.md](docs/FUNCTION_OVERVIEW.md) — 功能流程與服務架構說明
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — 版本記錄
- [docs/TODO.md](docs/TODO.md) — 未實作項目追蹤

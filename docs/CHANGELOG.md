# Changelog

## 2.11.0

架構重構版。主程式由單一 3300 行大類別拆分為服務層架構。

### 架構

- `VocalForgeStudioApp` 精簡至約 1270 行，只保留 GUI 建構與事件入口。
- 新增 `services/ffmpeg_service.py`：所有 FFmpeg 操作集中管理，字幕 input index 改為動態計算。
- 新增 `services/download_service.py`：yt-dlp 下載、字幕下載、JS runtime 偵測、cookie 設定。
- 新增 `services/separation_service.py`：audio-separator 執行、多音軌合併、暫存檔清理。
- 新增 `services/environment_service.py`：可攜式環境部署、AI 套件安裝、GPU 偵測與修復。
- 所有服務方法統一回傳 `TaskResult`。

### 新功能

- Tab 3（本地影片轉 KTV）新增字幕導入列：可手動選擇 `.srt`，或自動比對影片同名 `.srt`。

### 改善

- 環境安裝 pip 寫入階段新增 heartbeat log（每 20 秒），避免使用者誤判卡死。
- `_start_async_setup` 改用 `runner.start()`，背景執行緒統一由 TaskRunner 管理。
- 移除未使用的 import（`ttk`、`threading`、`urllib.request`、`zipfile`、`shutil` 等）。

## 2.10.1

- 專案產品化命名為 VocalForge KTV Studio。
- 主程式改名為 `vocalforge_ktv_studio.py`。
- 打包輸出名稱改為 `VocalForgeKTVStudio.exe`。
- 新增 `README.md` 與 `docs/FUNCTION_OVERVIEW.md`。
- `TaskResult` 移至 `services/task_result.py`，作為後續服務層共用結果物件。
- 修正純下載流程的成功/失敗判斷。
- 修正 `download_youtube` MP3 下載 tuple 判斷問題。
- 純下載分頁的彈窗與開啟資料夾流程改由 Tkinter 主執行緒執行。

## 2.10.0

- 整合 YouTube 下載、AI 音訊分離與 KTV 影片合成。
- 支援 CPU / NVIDIA GPU 運算環境。
- 支援 MKV / MP4 與 YouTube CC 字幕封裝。

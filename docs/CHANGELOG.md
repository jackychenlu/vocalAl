# Changelog

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

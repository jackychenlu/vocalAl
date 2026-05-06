# VocalForge KTV Studio

VocalForge KTV Studio 是一套 Windows 桌面工具，用於 YouTube 下載、AI 人聲/伴奏分離，以及 KTV 伴唱影片製作。

## 核心功能

- YouTube 一鍵製作 KTV 影片
- YouTube MP3 / MP4 純下載
- 本地音檔批次分離人聲與伴奏
- 本地影片批次轉 KTV 影片
- 支援 MKV / MP4 輸出
- 支援 YouTube CC 字幕下載與封裝
- 支援 CPU 與 NVIDIA GPU 運算環境
- 內建環境初始化與修復流程

## 專案結構

```text
vocalforge_ktv_studio.py      主程式與 Tkinter GUI
services/                    可逐步抽離的服務層
docs/FUNCTION_OVERVIEW.md    功能與架構說明
update.md                    修復與架構待辦追蹤
dist/                        PyInstaller 打包輸出
build/                       PyInstaller 建置中間檔
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

快速驗證：

```powershell
py vocalforge_ktv_studio.py --smoke-test
```

## 打包

```powershell
py -m PyInstaller --clean --onefile --windowed --name "VocalForgeKTVStudio" "vocalforge_ktv_studio.py"
```

輸出檔：

```text
dist/VocalForgeKTVStudio.exe
```

## 維護文件

詳細功能流程與維護注意事項請見：

- [docs/FUNCTION_OVERVIEW.md](docs/FUNCTION_OVERVIEW.md)
- [update.md](update.md)

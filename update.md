# 修復與架構檢查清單

本文記錄 `vocalforge_ktv_studio.py` 目前已發現的明顯邏輯問題與架構風險，作為後續修復依據。

## 一、優先修復的明顯邏輯問題

### 1. ✅ 純下載流程會把失敗顯示成成功（已修復）

位置：

- `pure_download_process`
- `pure_download_file`

~~問題：~~

- ~~`pure_download_process` 呼叫 `pure_download_file(...)` 後沒有接收回傳值。~~
- ~~`pure_download_file` 目前只寫日誌，沒有明確回傳成功/失敗或新產生的檔案路徑。~~
- ~~即使 MP3 或 MP4 下載失敗，`pure_download_process` 最後仍會顯示「所有任務已全部完成」與成功彈窗。~~

修復內容：

- 新增 `services/task_result.py` 的 `TaskResult` dataclass（`success`, `path`, `paths`, `error`），並由主程式引用同一份定義。
- `pure_download_file` 現在回傳 `TaskResult`：成功時帶最新產出檔路徑，失敗/取消時帶 error 說明。
- `pure_download_process` 捕捉兩個格式的結果，依實際成功狀態顯示成功彈窗或「部分失敗」警告。
- 分離流程只使用本次下載回傳的 `mp3_result.path`，不再仰賴時間戳猜測最新檔。
- 若 yt-dlp 回報成功但沒有新增檔案，會用本次 `video_id` 找既有輸出檔，避免「檔案已存在」被誤判為失敗。
- `messagebox` / `os.startfile` 改透過 `root.after(0, ...)` 在主執行緒執行。

## 二、架構檢查結果

### A. 單一類別承擔太多責任

目前 `VocalForgeStudioApp` 同時負責：

- GUI 建立與狀態更新。
- YouTube 下載。
- YouTube 字幕處理。
- FFmpeg 音訊擷取與影片合成。
- audio-separator AI 分離。
- CPU/GPU 環境診斷。
- 可攜式 Python / FFmpeg / pip / AI 套件安裝。
- 批次任務流程。
- 錯誤處理與日誌。

風險：

- 修改其中一個流程時，很容易影響其他流程。
- 測試困難，因為商業邏輯和 Tkinter widget 綁在一起。
- 任務狀態與 UI 狀態交錯，錯誤時不容易恢復。

建議拆分方向：

- `VocalForgeStudioApp`：只保留 GUI 與事件入口。
- `DownloadService`：YouTube metadata / MP3 / MP4 / 字幕下載。
- `SeparationService`：audio-separator 執行與模型參數。
- `FfmpegService`：抽音訊、合併 Demucs stems、KTV 合成。
- `EnvironmentService`：Python、FFmpeg、pip、AI 套件部署與診斷。
- `TaskRunner`：統一背景任務、取消、進度、成功/失敗結果。

### B. ✅ 缺少統一的任務結果物件（已部分修復）

目前各函式回傳不一致：

- 有些回傳 `True` / `False`。
- 有些回傳路徑字串或 `None`.
- ~~有些失敗只寫日誌、不回傳。~~（`pure_download_file` 已修復）
- 有些回傳 tuple，例如 `download_youtube(mode="both")`。

修復內容：

- 已新增 `services/task_result.py` 的 `TaskResult` dataclass 並應用於 `pure_download_file`。
- 主程式改為引用服務層的同一份 `TaskResult` 定義，避免同名類別分裂。
- `download_youtube` 的 MP3 tuple 判斷 bug 已修正（`mp3_ok, _mp3_errs = run_ytdlp_with_logging(...)`）。

待辦：其餘回傳 `True/False` 或 tuple 的流程尚未統一為 `TaskResult`。

### C. ✅ 背景執行緒直接操作 Tkinter 或彈窗（純下載分頁已修復）

目前部分背景任務中直接呼叫：

- ~~`pure_download_process` 中的 `messagebox.showinfo` / `os.startfile`~~（已修復，改用 `root.after`）
- `messagebox.askyesno`（環境安裝流程，尚未修復）
- `Listbox` selection 操作（尚未修復）
- 部分按鈕狀態更新（尚未修復）

待辦：環境安裝與 KTV 製作流程中的 messagebox 仍需逐一改為 `root.after`。

### D. 任務生命週期管理分散

目前 `is_processing`、`cancel_event`、`_current_process` 分散在多個流程中操作。

風險：

- 某些失敗分支可能沒有完整恢復 UI 狀態。
- `_current_process` 同一時間只能記錄一個子程序，若未來有平行步驟會不夠用。
- 取消時只能 terminate 當前 process，無法知道任務目前在哪個階段。

建議：

- 建立統一任務包裝：

```python
def start_task(self, status_text, target, *args):
    # 設定 is_processing、按鈕、日誌、取消狀態
    # 啟動 thread
    # finally 統一 finish_processing
```

- 所有任務都走同一個入口與 finally。

### E. YouTube 下載邏輯與檔案追蹤不夠可靠

目前純下載分頁使用 `%(title).100s.%(ext)s` 作為輸出模板，下載後用「新增檔案」判斷。

風險：

- 若檔案已存在，yt-dlp 可能跳過或覆蓋，導致找不到「新增檔案」。
- 不含 video id，重名影片較容易撞名。
- ~~下載後分離用「輸出資料夾最新 MP3」，可能拿到舊檔。~~（已修復：只使用本次 `mp3_result.path`）

建議：

- 純下載也統一使用 `<安全標題>_<video_id>.<ext>`。
- `pure_download_file` 回傳實際輸出檔。
- 分離只使用該次下載回傳的 MP3 路徑。

### F. 環境部署與執行環境耦合太重

目前程式啟動後會自動檢查組件，並在同一個 App 類別中處理大量安裝邏輯。

風險：

- 安裝流程和 GUI 流程互相干擾。
- 一鍵修復中途失敗時，按鈕狀態和 `is_processing` 可能沒有完全一致。
- 對打包後 EXE 的資料夾判斷與可寫入性要求高。

建議：

- 把環境檢查/安裝拆到 `EnvironmentService`。
- 每個安裝步驟回傳明確狀態。
- UI 只負責顯示目前步驟與是否可重試。

### G. ✅ PyInstaller / EXE 啟動參數保護過於粗暴（已修復）

~~目前程式開頭：~~

```python
# 舊程式碼（已移除）
if len(sys.argv) > 1 and not any(arg.startswith('--multiprocessing') for arg in sys.argv):
    sys.exit(0)
```

修復內容：

- 改成 `multiprocessing.freeze_support()` 先處理 worker 程序（worker 會在此之後自動 exit）。
- 明確支援 `--smoke-test` 參數作為 CI 快速驗證出口。
- 其他未知參數不再強制退出，讓 GUI 正常開啟（為未來 CLI / 拖曳支援保留空間）。

### H. FFmpeg 合成參數需要集中管理

目前 FFmpeg 命令散在：

- 本地影片抽音訊。
- YouTube 影片抽音訊。
- Demucs 多音軌混合。
- KTV MKV/MP4 合成。

風險：

- 相同抽音訊參數重複。
- 未來調整 bitrate、codec、錯誤處理時容易漏改。
- 字幕 input index 目前在 `synthesize_mkv` 中固定為 `3`，未來增加其他輸入時容易錯。

建議：

- 建立 `FfmpegService`。
- 用小函式產生命令：
  - `extract_audio(video, output_mp3)`
  - `merge_stems(stems, output)`
  - `make_ktv_video(...)`
- 字幕 input index 由實際 input list 長度計算。

## 三、建議修復順序

1. ✅ 修正 `download_youtube` 的 MP3 tuple 判斷錯誤。
2. ✅ 讓 `pure_download_file` 回傳成功狀態與輸出檔路徑（`TaskResult`）。
3. ✅ 修正 `pure_download_process` 的成功/失敗判斷與「下載後分離」檔案來源。
4. ✅ 本地音檔批次分離前建立輸出目錄。
5. 把其餘背景執行緒中的 messagebox / widget 操作改成 `root.after`。（純下載已完成，環境安裝待辦）
6. ✅ 建立統一 `TaskResult`（已移至 `services/task_result.py`，`pure_download_file` 已套用）。
7. 規劃服務層拆分，先從 `DownloadService` 與 `FfmpegService` 開始。（待辦）

## 四、目前暫不優先處理但應追蹤

- 支援命令列參數或拖曳檔案到 EXE。
- 將下載、分離、合成流程寫成可測試的純函式。
- 為 yt-dlp / FFmpeg / audio-separator 命令建立 dry-run 或 debug log 模式。
- 檢查所有檔案清理流程，避免失敗時刪掉仍需要的中間檔。
- 檢查路徑包含中文、空白、特殊符號時的所有 subprocess 行為。

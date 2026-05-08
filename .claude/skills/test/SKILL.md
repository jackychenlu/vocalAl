---
description: Run smoke tests to verify Python imports, SRT cleaning, TaskResult, and FFmpeg availability.
disable-model-invocation: true
allowed-tools: PowerShell(py *) PowerShell(Get-ChildItem *)
shell: powershell
---

# Test — 快速冒煙測試

針對 VocalForge KTV Studio 執行基本功能驗證（不需要 GPU）。

## 執行步驟

**1. 語法與匯入驗證**

```powershell
py -c "import vocalforge_ktv_studio; print('import OK')"
py -c "from services.download_service import DownloadService; print('download_service OK')"
py -c "from services.ffmpeg_service import FfmpegService; print('ffmpeg_service OK')"
py -c "from services.task_result import TaskResult; print('task_result OK')"
```

**2. FFmpeg 可用性**

```powershell
$ffmpeg = Get-ChildItem engine_ffmpeg -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if ($ffmpeg) { & $ffmpeg.FullName -version | Select-Object -First 1 } else { Write-Host "ffmpeg NOT FOUND" }
```

**3. yt-dlp 可用性**

```powershell
py -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
```

**4. SRT 清理邏輯**

```powershell
py -c "
from services.download_service import DownloadService
from pathlib import Path
test = Path('_test_sub.srt')
test.write_text('1\n00:00:01,000 --> 00:00:03,000\n<00:00:01.500><c>Hello</c> World\n', encoding='utf-8')
svc = DownloadService.__new__(DownloadService)
svc._clean_srt_file(str(test))
print(test.read_text(encoding='utf-8'))
test.unlink()
print('SRT clean OK')
"
```

**5. TaskResult**

```powershell
py -c "
from services.task_result import TaskResult
ok = TaskResult(success=True, path='/tmp/a.mp3')
err = TaskResult(success=False, error='test error')
assert ok.success and ok.path == '/tmp/a.mp3'
assert not err.success and err.error == 'test error'
print('TaskResult OK')
"
```

## 預期結果

所有步驟應輸出 `OK` 且無 `Exception`。

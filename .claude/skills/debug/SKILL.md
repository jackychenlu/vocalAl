---
description: Diagnose runtime issues by reading debug logs, checking FFmpeg output, GPU status, and recent output files. Use when investigating errors or crashes.
when_to_use: Trigger when the user reports a crash, error, or wants to see what happened during the last run.
allowed-tools: PowerShell(Get-Content *) PowerShell(Get-ChildItem *) PowerShell(py *)
shell: powershell
---

# Debug — 診斷執行期問題

## 執行步驟

**1. 查看最新 debug log**

```powershell
$log = "$env:USERPROFILE\AppData\Local\VocalForge\vocalforge_debug.log"
if (Test-Path $log) { Get-Content $log -Tail 100 } else { Write-Host "Debug log not found at: $log" }
```

**2. 搜尋 log 中的錯誤**

```powershell
$log = "$env:USERPROFILE\AppData\Local\VocalForge\vocalforge_debug.log"
Get-Content $log | Select-String "ERROR|Exception|Traceback|returncode=[^0]" | Select-Object -Last 30
```

**3. 確認 FFmpeg 可執行**

```powershell
$ffmpeg = Get-ChildItem engine_ffmpeg -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if ($ffmpeg) { & $ffmpeg.FullName -version 2>&1 | Select-Object -First 3 }
```

**4. 確認 GPU / CUDA 狀態**

```powershell
py -c "import torch; print('CUDA:', torch.cuda.is_available(), '| Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

**5. 檢查最近輸出**

```powershell
Get-ChildItem output\ -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

## 常見問題速查

| 症狀 | 看哪裡 |
|------|--------|
| 字幕無法封裝 | `[KTV-SUB]` log 行 |
| FFmpeg 合成失敗 | `[KTV-RC]` 非 0 行 |
| yt-dlp 下載失敗 | 執行 `/deps` 更新 yt-dlp |
| Demucs OOM | VRAM 不足，改 CPU 模式 |

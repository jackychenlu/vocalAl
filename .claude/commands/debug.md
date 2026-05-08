# /debug — 診斷執行期問題

當應用程式出現錯誤時，快速收集診斷資訊。

## 執行步驟

**1. 查看最新 debug log**

```powershell
$log = "$env:USERPROFILE\AppData\Local\VocalForge\vocalforge_debug.log"
if (Test-Path $log) {
    Get-Content $log -Tail 100
} else {
    Write-Host "Debug log not found at: $log"
}
```

**2. 查看所有 log 檔案大小**

```powershell
Get-ChildItem "$env:USERPROFILE\AppData\Local\VocalForge\" -ErrorAction SilentlyContinue
```

**3. 搜尋 log 中的錯誤**

```powershell
$log = "$env:USERPROFILE\AppData\Local\VocalForge\vocalforge_debug.log"
Get-Content $log | Select-String "ERROR|Exception|Traceback|returncode=[^0]" | Select-Object -Last 30
```

**4. 確認 FFmpeg 指令能正常執行**

```powershell
$ffmpeg = Get-ChildItem engine_ffmpeg -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if ($ffmpeg) {
    & $ffmpeg.FullName -version 2>&1 | Select-Object -First 3
}
```

**5. 檢查輸出資料夾**

```powershell
Get-ChildItem output\ -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

**6. 確認 GPU / CUDA 狀態**

```powershell
py -c "import torch; print('CUDA:', torch.cuda.is_available(), '| Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

## 常見問題速查

| 症狀 | 可能原因 | 指令 |
|------|----------|------|
| 字幕無法封裝 | 路徑含特殊字元或檔案已刪除 | 看 `[KTV-SUB]` log 行 |
| FFmpeg 合成失敗 | 編碼器不支援 / 檔案損毀 | 看 `[KTV-RC]` 非 0 |
| yt-dlp 下載失敗 | YouTube 反爬蟲更新 | 執行 `/deps` 更新 yt-dlp |
| Demucs OOM | VRAM 不足 | 改用 CPU 模式或較小模型 |

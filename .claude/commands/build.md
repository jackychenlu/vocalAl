# /build — 打包 Windows EXE（PyInstaller）

將 VocalForge KTV Studio 打包為獨立執行檔。

## 執行步驟

**1. 確認環境**

```powershell
py -m PyInstaller --version
py -c "import customtkinter; print(customtkinter.__file__)"
```

**2. 清理舊的建置產物**

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
```

**3. 執行打包**

```powershell
py -m PyInstaller `
  --noconfirm `
  --windowed `
  --icon=assets/icon.ico `
  --name "VocalForge KTV Studio" `
  --add-data "assets;assets" `
  --hidden-import customtkinter `
  --collect-data customtkinter `
  --collect-data darkdetect `
  --exclude-module ai_libraries `
  --exclude-module ai_libraries_gpu `
  vocalforge_ktv_studio.py
```

> 若 `assets/icon.ico` 不存在，移除 `--icon` 參數。

**4. 驗證輸出**

```powershell
Get-ChildItem dist\
& "dist\VocalForge KTV Studio\VocalForge KTV Studio.exe"
```

## 注意事項

- `runtime_python/`, `ai_models/`, `engine_ffmpeg/`, `output/` 資料夾**不打包**，應由使用者自備或安裝程式另行處理
- customtkinter 需透過 `--collect-data` 才能正確攜帶主題 JSON
- 若出現 `ModuleNotFoundError` 於執行時，用 `--hidden-import <模組名>` 補充
- Windows Defender 可能對新 EXE 觸發警告 — 預期行為，非惡意程式碼

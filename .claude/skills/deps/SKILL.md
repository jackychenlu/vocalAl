---
description: Check and update Python dependencies for VocalForge KTV Studio. Use when packages may be outdated, after install errors, or before release.
disable-model-invocation: true
allowed-tools: PowerShell(py *)
shell: powershell
---

# Deps — 檢查與更新依賴套件

檢查 VocalForge KTV Studio 所有 Python 依賴的安裝狀態與版本。

## 執行步驟

**1. 核心依賴狀態**

```powershell
py -m pip show customtkinter yt-dlp torch torchaudio demucs ruff pyright PyInstaller 2>&1 | Select-String "^(Name|Version|---)"
```

**2. 檢查過期套件**

```powershell
py -m pip list --outdated
```

**3. 更新 yt-dlp（最常需要更新）**

```powershell
py -m pip install --upgrade yt-dlp
py -c "import yt_dlp; print('yt-dlp updated to', yt_dlp.version.__version__)"
```

**4. 更新開發工具**

```powershell
py -m pip install --upgrade ruff pyright
```

**5. （選用）更新全部 — 謹慎使用，torch/demucs 升級可能破壞模型相容性**

```powershell
# py -m pip install --upgrade customtkinter torch torchaudio demucs
```

## 必要套件清單

| 套件 | 用途 |
|------|------|
| `customtkinter` | 現代化 CTk UI 元件 |
| `yt-dlp` | YouTube 下載核心 |
| `torch` + `torchaudio` | Demucs AI 分離引擎 |
| `demucs` | 人聲/伴奏分離模型 |
| `ruff` | Lint + 格式化 |
| `pyright` | 靜態型別檢查 |
| `PyInstaller` | Windows EXE 打包 |

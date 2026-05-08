---
description: Run Ruff lint/format check and Pyright type checking on Python source files. Use when checking code quality, after editing Python files, or before committing.
when_to_use: Trigger when the user asks to check code quality, lint, format, or type-check Python files.
allowed-tools: PowerShell(py *)
shell: powershell
---

# Lint — Python 代碼品質檢查

針對 VocalForge KTV Studio 執行完整代碼品質檢查。

## 執行步驟

**1. Ruff — Lint 與格式檢查**

```powershell
py -m ruff check . --exclude runtime_python,ai_libraries,ai_libraries_gpu,ai_models,engine_ffmpeg,output,build,dist
py -m ruff format . --check --exclude runtime_python,ai_libraries,ai_libraries_gpu,ai_models,engine_ffmpeg,output,build,dist
```

**2. Pyright — 靜態型別檢查**

```powershell
py -m pyright vocalforge_ktv_studio.py services/
```

## 審查重點

- 未使用的 import 與變量
- 型別注解不一致（特別是 `Callable | None` 等）
- 潛在的 `None` 解引用
- 格式問題（縮排、空行、行長）

報告所有發現的問題，並按嚴重程度分類（Error / Warning / Info），提供修復建議。

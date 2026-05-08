---
model: claude-sonnet-4-6
---

# Security Scan — Python 安全掃描

針對 VocalForge KTV Studio（Python 桌面應用）執行安全漏洞檢查。

## 執行步驟

### 1. 安裝掃描工具（若未安裝）

```powershell
py -m pip install bandit pip-audit
```

### 2. Bandit — Python 靜態安全分析

```powershell
py -m bandit -r vocalforge_ktv_studio.py services/ -f txt -ll
```

重點關注：
- B603 `subprocess` 呼叫（shell injection 風險）
- B607 執行外部程式
- B110/B112 except pass / except continue
- B324 使用弱雜湊演算法

### 3. pip-audit — 依賴套件 CVE 掃描

```powershell
py -m pip_audit --desc
```

### 4. 人工審查重點

針對本專案的特定風險：

**subprocess / FFmpeg 指令注入**
- 確認所有傳入 FFmpeg 的路徑都來自使用者選檔（非直接輸入文字）
- 確認 `subprocess.run()` 使用 list 形式（不用 `shell=True`）

**yt-dlp URL 驗證**
- 確認 YouTube URL 僅接受 `https://` 開頭
- 確認不允許 `file://` 或 `javascript:` 等 scheme

**檔案路徑穿越**
- 確認輸出路徑限制在 `output/` 資料夾內
- 確認 subtitle 路徑來自已知目錄

**臨時檔案**
- 確認所有 tempfile 使用後都有 `unlink()`（或用 context manager）

## 報告格式

依嚴重程度分類：
- **HIGH** — 可能導致遠端代碼執行或資料洩漏
- **MEDIUM** — 本地安全問題或資訊洩露
- **LOW** — 最佳實踐建議

$ARGUMENTS

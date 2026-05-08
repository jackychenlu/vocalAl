# /lint — Python 代码质量检查

针对 VocalForge KTV Studio 专案执行完整代码质量检查。

## 执行步骤

**1. Ruff — Lint 与格式检查**

```bash
py -m ruff check . --exclude runtime_python,ai_libraries,ai_libraries_gpu,ai_models,engine_ffmpeg,output,build,dist
py -m ruff format . --check --exclude runtime_python,ai_libraries,ai_libraries_gpu,ai_models,engine_ffmpeg,output,build,dist
```

**2. Pyright — 静态类型检查**

```bash
py -m pyright vocalforge_ktv_studio.py services/
```

## 审查重点

- 未使用的 import 与变量
- 类型注解不一致（特别是 `Callable | None` 等）
- 潜在的 `None` 解引用
- 格式问题（缩进、空行、行长）

报告所有发现的问题，并按严重程度分类（Error / Warning / Info），提供修复建议。

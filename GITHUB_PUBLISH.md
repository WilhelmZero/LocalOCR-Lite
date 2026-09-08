# 将 Lite 版发布到 GitHub

建议把 **解压后的 Lite 目录内容作为仓库根目录**，而不是把整个旧完整版或一个 ZIP 当作源码仓库。此目录应含 README.md、localocr/、tools/、安装脚本、依赖与模型版本清单、第三方说明和 .gitignore。

## 发布内容

- 源码提交到仓库；轻量 ZIP 可作为 GitHub Release 附件，方便非开发用户下载。
- 不提交 `.venv/`、`runtime/`、`data/`、模型、个人图片、导出内容、数据库、日志、访问令牌或完整工作目录。
- 本包 `.gitignore` 会忽略常见运行产物。它不会自动移除已经被 Git 跟踪的文件；提交前仍应检查暂存区。
- 不需要为这些不应提交的模型配置 Git LFS；模型应继续由应用按需下载。

在 GitHub 创建空仓库后，将其提供的仓库地址用于本地 Git。以下仅为命令模板，请先替换地址；本说明不会替你创建仓库或推送。

```bash
git init
git add .
git status --short
git diff --cached --stat
git diff --cached
# 检查提交范围后，再运行：
git commit -m "Initial Lite release"
git branch -M main
git remote add origin <你的GitHub仓库地址>
git push -u origin main
```

如果目录本来就是 Git 仓库，请沿用已有分支和 remote，不要重复创建。

## 许可证

本项目自有代码采用 MIT License，版权署名为 WilhelmZero。分发时保留 LICENSE 和第三方组件、模型的独立许可说明。第三方模型或 Qt 的许可不因本项目的 MIT 授权而改变。

## Release 说明建议

> 识文 Lite 1.3.0：本地批量 OCR 与人工校对。先安装 Python 3.12，再运行安装脚本；通过“模型下载”按需安装基础、表格或增强功能。ZIP 不含 Python、模型或 CUDA。Windows 主要流程已验证；macOS Apple Silicon CPU 安装脚本仍待真机验收，Intel Mac 与 Mac VL 暂不支持。模型精度与运行速度因图片和设备而异。

发布前在全新可写目录解压安装包，检查 README 链接、安装入口和“模型下载”窗口。不要把本机已缓存模型的测试描述为全新联网安装验证；也不要将 Windows 测试结果当作 Mac 验收结果。

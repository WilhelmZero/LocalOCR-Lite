# Apple Silicon DMG 构建

在 **Apple Silicon Mac** 上准备原生 ARM64 Python 3.12 和 Xcode Command Line Tools，然后于项目根目录运行：

```bash
bash tools/build_mac_dmg.sh
```

成功后得到 `release/LocalOCR-1.3.0-Apple-Silicon.dmg`。DMG 内的 `LocalOCR.app` 包含 Python 3.12、桌面依赖和 CPU 版 OCR 运行依赖；使用者无需安装系统 Python，因此系统中其他 Python 3 版本不会影响应用。首次使用仍需在应用内选择并下载对应公开 OCR 模型。校对数据和模型保存在 `~/Library/Application Support/LocalOCR`，不写入只读 DMG 或应用包。

此构建面向 ARM64 Mac 的 CPU 推理。文档 VL 和代码 VL 仍未在 Mac 版开放。脚本运行自检、签署临时签名并验证 DMG；临时签名**不是 Apple Developer ID 签名或公证**，分发给其他用户之前需要在 Mac 上补做真实签名、公证及真机 OCR 验收。Python 包版本来自本项目的依赖约束，不能声称源码兼容所有 Python 3 小版本。

# 识文 1.3：轻量安装与按需模型

轻量 ZIP 仅含应用源码、安装/启动脚本、固定版本依赖入口和许可说明。不包含 Python、模型、CUDA、开发工具、测试图、日志或用户校对数据。首次需要自行安装 64 位 Python 3.12。UI 运行依赖需要数百 MB；选择 OCR 功能后再下载对应依赖和模型（数百 MB 到数 GB），ZIP 大小不等于最终磁盘占用。

## Windows

解压到可写目录，安装 Python 3.12（python.org），双击“安装轻量版.cmd”。安装只创建本目录 .venv 并安装桌面、去水印、导出依赖。之后双击“启动.cmd”。

点击顶部“模型下载”，选“基础文字 / 代码”和 CPU，保留“同时安装所选功能的运行依赖”，点击“下载 / 安装 / 重试”。安装失败保留日志，可重试。NVIDIA GPU 单独选择，会额外下载 CUDA 相关依赖；CPU / GPU 切换会更换该安装目录的 Paddle 包。

打印和代码共用 PP-OCRv6 medium；拍照另需方向校正模型；表格按需安装解析依赖与结构模型；文档 VL 和代码 VL 单独下载。下载进程只对构造空白页验证，不读取用户图片。完成后保留“允许下载模型”未勾选，以离线方式使用。界面“取消下载”停止本次进程，重试由上游下载器复用有效缓存，不保证所有源均支持字节级续传。

旧完整版仍可继续使用，不必重新下载已有模型。旧便携运行环境不含 pip，应取消“同时安装运行依赖”，只下载模型；如需切换运行依赖，使用新的轻量安装目录。旧校对数据不会被轻量包覆盖或自动迁移。

## macOS：Apple Silicon CPU 适配版（待真机验收）

先安装原生 ARM64 Python 3.12，避免在 Rosetta 的 x86 Python 下安装。解压到可写目录，在终端运行：

```bash
cd "/你的路径/LocalOCR-Lite-1.3.0"
bash 安装.command
bash 启动.command
```

ZIP 为 .command 文件保留可执行权限，也可双击。Finder 或系统安全提示可能需要按本机设置处理。启动、后台识别进程、文件路径和错误日志已改为跨平台；默认 CPU。Qt 窗口、文件夹扫描、去水印、校对和导出共用源码。

当前固定 Paddle 3.3.1 的 macOS wheel 仅有 ARM64，因此本版不支持 Intel Mac OCR。Apple Silicon 的基础/拍照/表格功能提供安装路径，但本次没有 Mac 硬件，未完成真机推理与打包验收；不能当作已验证的 macOS .app。文档 VL、代码 VL 在 Mac 暂不开放，未接入 MLX/Metal，不把 Windows CUDA 路径用于 Mac。

依据：[Paddle 3.3.1 官方发布文件](https://pypi.org/project/paddlepaddle/3.3.1/)、[PaddleOCR Apple Silicon VL 文档](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html)。后者的 Apple Silicon 推理需要单独适配，不等于当前应用已接入。

## 版本与数据

UI 顶层依赖由 requirements-ui.lock.txt 固定。OCR 顶层版本由 localocr/model_setup.py 固定；跨平台传递依赖由 pip 在本机解析，未声称为 macOS 完整锁文件。安装 UI 后记录 data/installed-ui.txt、platform.json；模型完成后记录 data/model-功能.json（平台、设备、包版本和本机验证时间）。代码 VL 使用固定模型 revision 和权重 SHA256。基础模型采用 PaddleOCR/PaddleX 固定版本选型及上游缓存。

不自动删掉旧版本的模型和用户数据。分发请使用新的 Lite ZIP；不要将旧 release/LocalOCR 的整个目录再次压缩发送。轻量安装本身不降低识别模型精度。

# 开源项目与第三方组件

Lite 分发包仅包含本应用源码与安装入口，不包含下述模型、第三方二进制、字体或验收样图。依赖与模型由用户另行下载；其许可仍各自适用。本文中的完整运行目录、历史锁文件和示例路径指旧完整版，并不表示 Lite ZIP 包含这些文件。本项目自有代码采用 MIT License，第三方许可不受此授权影响。

- PaddleOCR / PaddleX / PaddlePaddle：Apache-2.0。采用官方发行包与模型，下载地址由官方模型注册表解析。
  - https://github.com/PaddlePaddle/PaddleOCR
  - https://github.com/PaddlePaddle/PaddleX
  - https://github.com/PaddlePaddle/Paddle
- PP-OCRv6：https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html
- PP-StructureV3：https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddleOCR-VL：https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html
- PySide6 / Qt：LGPLv3 / GPLv3 / 商业许可；本发布目录动态加载 Qt，保留组件原有许可文件。
- Pillow：HPND；openpyxl：MIT；Python：PSF License。
- NVIDIA CUDA / cuDNN 运行库：按 NVIDIA 随包许可分发，非本项目自有代码。
- Umi-OCR 与 RapidOCR 仅作为交互及部署方案参考，没有复制其应用源码：
  - https://github.com/hiroi-sora/Umi-OCR
  - https://github.com/RapidAI/RapidOCR

完整直接与间接依赖见 requirements.lock.txt；发布目录保留各 Python 发行包的 dist-info 及其许可证。模型目录中的许可证与说明亦保留。再分发时应随软件保留这些文件。

英文手写验收图片来自 SimpleHTR 的公开 data/line.png（Harald Scheidl，MIT）：https://github.com/githubharald/SimpleHTR 。其余演示图片由本项目使用系统字体生成。SimpleHTR 的许可正文随示例放在 examples/SimpleHTR-LICENSE.md。
# 代码 OCR 扩展

- `snnh/paddleocr_vl_code_ocr`，Apache-2.0，权重锁定 revision `bdf898bb154a7b1cdfb74ae6cab39bf7e13277e7`。模型说明随模型目录保存。来源：https://huggingface.co/snnh/paddleocr_vl_code_ocr 。PaddleOCR-VL-1.6 的代码场景微调模型。
- CodeOCR：https://github.com/mdoumbouya/codeocr ，仅作为代码缩进恢复的设计参考，未复制实现代码或数据集。
- 本次新增代码样本由项目脚本合成，不包含第三方截图。中文与等宽字体使用本机 Windows 字体，字体文件不随项目重新分发。

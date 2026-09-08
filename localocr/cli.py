"""Headless recognition and reproducible accuracy measurements."""
import argparse
import json
import platform
from pathlib import Path

from .core import app_data, file_hash, uid


def cer(reference, prediction):
    """Levenshtein edits / reference characters; whitespace preserved except newlines."""
    reference, prediction = reference.replace("\r", "").replace("\n", ""), prediction.replace("\r", "").replace("\n", "")
    previous = list(range(len(prediction)+1))
    for i, a in enumerate(reference, 1):
        current = [i]
        for j, b in enumerate(prediction, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(a != b)))
        previous = current
    return {"edits": previous[-1], "characters": len(reference),
            "cer": previous[-1]/len(reference) if reference else None}


def samples(destination):
    from PIL import Image, ImageDraw, ImageFont
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 36)
    small = ImageFont.truetype(str(font_path), 28)
    text = "本地文字识别与人工核对\nLocal OCR 2026 测试报告\n订单编号：AB123456\n识别结果保存在本地电脑。"
    image = Image.new("RGB", (1200, 600), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(text.splitlines()):
        draw.text((65, 60+i*95), line, fill="black", font=font)
    image.save(destination / "中文印刷.png")
    (destination / "中文印刷.txt").write_text(text, encoding="utf-8")
    image.rotate(5, expand=True, fillcolor="white").save(destination / "倾斜拍照.png")
    (destination / "倾斜拍照.txt").write_text(text, encoding="utf-8")
    table = Image.new("RGB", (1200, 750), "white")
    d = ImageDraw.Draw(table)
    d.text((90, 35), "采购物品明细表", fill="black", font=font)
    for y in [140, 240, 340, 440, 540]:
        d.line((80, y, 1120, y), fill="black", width=3)
    for x in [80, 1120]:
        d.line((x, 140, x, 540), fill="black", width=3)
    for x in [550, 820]:
        d.line((x, 240, x, 540), fill="black", width=3)
    d.text((420, 165), "本月采购汇总", fill="black", font=font)
    for row, values in enumerate([["品名", "数量", "金额"], ["打印纸", "10", "250.00"], ["文件夹", "20", "100.00"]]):
        for x, value in zip([105, 575, 845], values):
            d.text((x, 265+row*100), value, fill="black", font=small)
    table.save(destination / "合并单元格.png")
    columns = Image.new("RGB", (1400, 900), "white")
    cd = ImageDraw.Draw(columns)
    left = ["第一部分 项目介绍", "本项目支持本地文字识别。", "选择文件夹即可批量处理。", "识别之后可以人工校对。"]
    right = ["第二部分 使用说明", "图片与文字支持联动查看。", "修改结果自动保存到本地。", "支持导出文本以及表格。"]
    for x, lines in [(65, left), (750, right)]:
        for i, line in enumerate(lines):
            cd.text((x, 80+i*100), line, font=small, fill="black")
    columns.save(destination / "双栏文档.png")
    (destination / "双栏文档.txt").write_text("\n".join(left+right), encoding="utf-8")
    image.copy().save(destination / "多页.tiff", save_all=True, append_images=[table.copy()], compression="tiff_lzw")
    nested = destination / "子目录"
    nested.mkdir(exist_ok=True)
    image.save(nested / "中文印刷.png")
    (destination / "损坏.png").write_bytes(b"not an image")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sample = sub.add_parser("samples")
    sample.add_argument("destination")
    run = sub.add_parser("recognize")
    run.add_argument("image")
    run.add_argument("--mode", choices=["print", "photo", "structure", "code"], default="print")
    run.add_argument('--code-config', help='代码设置 JSON 文件（语言、图片类型、区域与水印）')
    run.add_argument('--watermark-config',help='识别前按颜色去水印的 JSON 设置')
    run.add_argument("--device", default="auto")
    run.add_argument("--enhance", action="store_true")
    run.add_argument("--download", action="store_true")
    run.add_argument("--reference")
    run.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "samples":
        return samples(args.destination)
    from .engines import configure_environment, Engine
    from .worker import execute
    from .exporting import render_text
    import importlib.metadata
    configure_environment(app_data(), not args.download)
    job = {"job_id": uid(), "path": str(Path(args.image).resolve()), "page": 0,
           "fingerprint": file_hash(args.image), "mode": args.mode, "device": args.device,
           "enhance": args.enhance, "data_dir": str(app_data())}
    if args.code_config:
        job['code_options'] = json.loads(Path(args.code_config).read_text(encoding='utf-8'))
    if args.watermark_config:
        job['color_watermark']=json.loads(Path(args.watermark_config).read_text(encoding='utf-8'))
    doc = execute(Engine(), job)
    packages = {}
    for package in ["paddleocr", "paddlex", "paddlepaddle", "paddlepaddle-gpu", "PySide6", "Pillow"]:
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    report = {"document": doc, "text": render_text(doc), "python": platform.python_version(), "packages": packages}
    if args.reference:
        report["accuracy"] = cer(Path(args.reference).read_text(encoding="utf-8"), report["text"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "text": report["text"], "accuracy": report.get("accuracy")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Local Paddle adapters. Engine output is normalized before crossing the process boundary."""
from __future__ import annotations

import gc
import json
import math
import os
import time
from html.parser import HTMLParser
from pathlib import Path

from .core import block, fill_table, new_document, uid


def polygon(value):
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not value:
        return []
    if isinstance(value[0], (float, int)):
        if len(value) == 4:
            x1, y1, x2, y2 = value
            return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        return [[value[i], value[i+1]] for i in range(0, len(value)-1, 2)]
    return [[float(p[0]), float(p[1])] for p in value]


def score(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and 0 <= value <= 1 else None
    except (TypeError, ValueError):
        return None


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells, self.row, self.col, self.current, self.occupied = [], -1, 0, None, set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row += 1
            self.col = 0
        elif tag in {"td", "th"}:
            self.row = max(0, self.row)
            while (self.row, self.col) in self.occupied:
                self.col += 1
            def span(key):
                try:
                    return max(1, min(1000, int(attrs.get(key, 1))))
                except ValueError:
                    return 1
            self.current = {"row": self.row, "col": self.col, "rowspan": span("rowspan"),
                            "colspan": span("colspan"), "text": "", "polygon": [], "confidence": None}
        elif tag == "br" and self.current is not None:
            self.current["text"] += "\n"

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.current is not None:
            cell = self.current
            cell["text"] = cell["text"].strip()
            self.cells.append(cell)
            for row in range(cell["row"], cell["row"] + cell["rowspan"]):
                for col in range(cell["col"], cell["col"] + cell["colspan"]):
                    self.occupied.add((row, col))
            self.col += cell["colspan"]
            self.current = None


def parse_table(markup, boxes=None, region=None):
    parser = TableParser()
    parser.feed(markup)
    table = {"id": uid(), "cells": parser.cells, "polygon": polygon(region)}
    if boxes is not None and len(boxes) == len(parser.cells):
        # Only use a one-to-one cell mapping; never fabricate fine-grained coordinates.
        for cell, box in zip(parser.cells, boxes):
            cell["polygon"] = polygon(box)
    fill_table(table)
    return table


def column_order(blocks, page_width):
    """Recover unambiguous newspaper columns from genuine OCR boxes.

    Only cut a wide empty vertical gutter when both sides have multiple lines
    with overlapping vertical extents; otherwise preserve the model's order.
    """
    if len(blocks) < 4 or any(not b["polygon"] for b in blocks):
        return blocks
    boxes = []
    for b in blocks:
        xs, ys = zip(*b["polygon"])
        boxes.append((min(xs), min(ys), max(xs), max(ys), b))
    intervals = []
    for left, _, right, _, _ in sorted(boxes, key=lambda x: x[0]):
        if intervals and left <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], right)
        else:
            intervals.append([left, right])
    gaps = sorted([(b[0]-a[1], (b[0]+a[1])/2) for a, b in zip(intervals, intervals[1:])], reverse=True)
    for gap, split in gaps:
        if gap < max(30, page_width*.04):
            continue
        left = [b for b in boxes if b[2] < split]
        right = [b for b in boxes if b[0] > split]
        if min(len(left), len(right)) < 2:
            continue
        overlap = min(max(b[3] for b in left), max(b[3] for b in right)) - max(min(b[1] for b in left), min(b[1] for b in right))
        if overlap <= 0:
            continue
        return column_order([b[4] for b in sorted(left, key=lambda b: (b[1], b[0]))], page_width) + column_order([b[4] for b in sorted(right, key=lambda b: (b[1], b[0]))], page_width)
    return blocks


def normalize_result(raw, width, height, engine):
    data = raw.get("res", raw)
    doc = new_document(width, height, engine=engine)
    parsing = data.get("parsing_res_list", [])
    if parsing:
        table_results = list(data.get("table_res_list", []))
        overall = data.get("overall_ocr_res", {})
        ocr_lines = []
        for index, text in enumerate(overall.get("rec_texts", [])):
            polys = overall.get("rec_polys", [])
            scores = overall.get("rec_scores", [])
            if index < len(polys):
                ocr_lines.append(block(text, polygon(polys[index]), score(scores[index]) if index < len(scores) else None))
        used_lines = set()
        for item in parsing:
            kind = item.get("block_label", "text")
            text = item.get("block_content", "")
            region = polygon(item.get("block_bbox"))
            b = block(text, region, kind=kind)
            if kind in {"text", "paragraph_title", "doc_title", "title", "header", "footer", "reference_content", "abstract"} and region:
                xs, ys = zip(*region)
                matches = []
                for line in ocr_lines:
                    if line["id"] in used_lines:
                        continue
                    points = line["polygon"]
                    cx, cy = sum(p[0] for p in points)/len(points), sum(p[1] for p in points)/len(points)
                    if min(xs)-3 <= cx <= max(xs)+3 and min(ys)-3 <= cy <= max(ys)+3:
                        line["kind"] = kind
                        line["layout_region"] = region
                        matches.append(line)
                        used_lines.add(line["id"])
                if matches:
                    doc["blocks"].extend(matches)
                    continue
            if kind == "table" and "<table" in text.lower():
                table = parse_table(text, region=item.get("block_bbox"))
                signature = [(c["text"], c["row"], c["col"], c["rowspan"], c["colspan"]) for c in table["cells"]]
                matches = []
                for detail in table_results:
                    parsed = parse_table(detail.get("pred_html", ""))
                    if [(c["text"], c["row"], c["col"], c["rowspan"], c["colspan"]) for c in parsed["cells"]] == signature:
                        matches.append(detail)
                # Different tables can share identical contents; ambiguous matches remain region-level.
                if len(matches) == 1:
                    detail = matches[0]
                    table_results.remove(detail)
                    table = parse_table(text, detail.get("cell_box_list"), item.get("block_bbox"))
                    ocr = detail.get("table_ocr_pred", {})
                    for cell in table["cells"]:
                        for index, recognized in enumerate(ocr.get("rec_texts", [])):
                            if recognized == cell["text"] and cell["polygon"] and index < len(ocr.get("rec_polys", [])):
                                points = polygon(ocr["rec_polys"][index])
                                cx, cy = sum(p[0] for p in points)/len(points), sum(p[1] for p in points)/len(points)
                                xs, ys = zip(*cell["polygon"])
                                if min(xs) <= cx <= max(xs) and min(ys) <= cy <= max(ys):
                                    scores = ocr.get("rec_scores", [])
                                    cell["confidence"] = score(scores[index]) if index < len(scores) else None
                doc["tables"].append(table)
                b["table_id"] = table["id"]
                b["text"] = "[表格]"
            doc["blocks"].append(b)
        ordered = column_order(doc["blocks"], width)
        if [b["id"] for b in ordered] != [b["id"] for b in doc["blocks"]]:
            doc["reading_order_method"] = "vertical_gutter"
        doc["blocks"] = ordered
    else:
        ocr = data.get("overall_ocr_res", data)
        boxes = ocr.get("rec_polys", ocr.get("rec_boxes", []))
        scores = ocr.get("rec_scores", [])
        for i, text in enumerate(ocr.get("rec_texts", [])):
            doc["blocks"].append(block(text, polygon(boxes[i]) if i < len(boxes) else [],
                                       score(scores[i]) if i < len(scores) else None))
        for detail in data.get("table_res_list", []):
            table = parse_table(detail.get("pred_html", ""), detail.get("cell_box_list"))
            doc["tables"].append(table)
    return doc


def configure_environment(data_dir, offline):
    data_dir = Path(data_dir)
    cache = Path(os.environ.get("LOCAL_OCR_MODELS", data_dir / "models")).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)
    os.environ["HF_HOME"] = str(cache / "huggingface")
    os.environ["MODELSCOPE_CACHE"] = str(cache / "modelscope")
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_MODEL_SOURCE"] = "BOS"
    os.environ["FLAGS_use_mkldnn"] = "0"
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        # Fail fast if a library tries downloading a missing model. Inference has no remote fallback.
        import socket
        def denied(*args, **kwargs):
            raise OSError("离线模式：所需模型未安装或依赖尝试联网。请在模型安装模式下重试。")
        socket.socket.connect = denied
        socket.socket.connect_ex = denied


class Engine:
    def __init__(self):
        self.pipeline, self.key = None, None

    def release(self):
        if self.pipeline is not None:
            try:
                self.pipeline.close()
            except Exception:
                pass
        self.pipeline = None
        self.key = None
        gc.collect()
        try:
            import paddle
            if paddle.is_compiled_with_cuda():
                paddle.device.cuda.empty_cache()
        except Exception:
            pass

    def run(self, image_path, mode, device, data_dir, enhance=False):
        if mode == 'code' and enhance:
            return self.run_code_vl(image_path, device, data_dir)
        import paddle
        from paddleocr import PaddleOCR, PPStructureV3, PaddleOCRVL
        from PIL import Image
        from .compat import install_paddle_unicode_fix
        install_paddle_unicode_fix()

        if device == "auto":
            device = "gpu:0" if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() else "cpu"
        key = (mode, device, enhance)
        if key != self.key:
            self.release()
            common = {"device": device, "use_doc_orientation_classify": mode == "photo" and not enhance,
                      "use_doc_unwarping": False}
            if enhance:
                self.pipeline = PaddleOCRVL(pipeline_version="v1.6", device=device,
                                            use_doc_orientation_classify=False, use_doc_unwarping=False,
                                            use_queues=False, enable_mkldnn=False,
                                            paddlex_config=self.vl_config())
                name = "PaddleOCR-VL-1.6"
            elif mode == "structure":
                self.pipeline = PPStructureV3(**common, use_formula_recognition=False,
                                               use_seal_recognition=False, use_chart_recognition=False,
                                               use_table_recognition=True, enable_mkldnn=False)
                name = "PP-StructureV3"
            else:
                self.pipeline = PaddleOCR(**common,
                                          text_detection_model_name="PP-OCRv6_medium_det",
                                          text_recognition_model_name="PP-OCRv6_medium_rec",
                                          use_textline_orientation=True, enable_mkldnn=False,
                                          text_recognition_batch_size=1)
                name = "PP-OCRv6_medium"
            self.key, self.name = key, name
        started = time.perf_counter()
        results = list(self.pipeline.predict(str(image_path), **({"max_new_tokens": 4096, "max_pixels": 1280*1280} if enhance else {})))
        if not results:
            raise RuntimeError("模型没有返回任何页面结果")
        result = results[0]
        raw = result.json
        if isinstance(raw, str):
            raw = json.loads(raw)
        with Image.open(image_path) as image:
            width, height = image.size
        doc = normalize_result(raw, width, height, self.name)
        doc["raw_result"] = raw
        pre = result.get("doc_preprocessor_res", {})
        output_image = pre.get("output_img") if pre else None
        if output_image is not None:
            import cv2
            import numpy as np
            view = Path(data_dir) / "previews" / (uid() + "-corrected.png")
            view.parent.mkdir(parents=True, exist_ok=True)
            ok, encoded = cv2.imencode(".png", output_image)
            if not ok:
                raise RuntimeError("无法保存校正视图")
            encoded.tofile(str(view))
            doc["view_path"] = str(view)
            doc["height"], doc["width"] = output_image.shape[:2]
            angle = pre.get("angle", 0)
            doc["coordinate_space"] = "corrected" if angle not in (0, -1, None) else "exif_normalized"
            doc["transform"].append({"type": "orientation", "angle": int(angle or 0)})
        doc.update(device=device, inference_seconds=round(time.perf_counter()-started, 3))
        doc["settings"] = {"batch_size": 1, "max_pixels": 1280*1280 if enhance else None,
                           "max_new_tokens": 4096 if enhance else None, "model_family": self.name}
        try:
            doc["peak_gpu_bytes"] = int(paddle.device.cuda.max_memory_allocated()) if device.startswith("gpu") else None
        except Exception:
            doc["peak_gpu_bytes"] = None
        return doc

    def run_code_vl(self, image_path, device, data_dir):
        import paddle
        from paddlex import create_model
        from PIL import Image
        from .core import new_document, block
        from .codeocr import MODEL_REVISION, prepare_model
        from .compat import install_paddle_unicode_fix
        install_paddle_unicode_fix()
        folder = Path(os.environ.get('LOCAL_OCR_MODELS', Path(data_dir)/'models')) / 'code-vl'
        if not (folder / 'model.safetensors.index.json').exists():
            raise RuntimeError('代码增强模型未安装。请运行 tools/install_code_model.py；基础代码识别仍可使用。')
        prepare_model(folder)
        if device == 'auto':
            device = 'gpu:0' if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() else 'cpu'
        key = ('code-vl', device, str(folder))
        if self.key != key:
            self.release()
            self.pipeline = create_model('PaddleOCR-VL-1.6-0.9B', model_dir=str(folder), device=device, batch_size=1, engine='paddle_dynamic')
            self.key = key
        start = time.perf_counter()
        raw = list(self.pipeline.predict({'image': str(image_path), 'query': 'OCR:'}, max_new_tokens=4096, max_pixels=1280*1280))[0].json
        if isinstance(raw, str):
            raw = json.loads(raw)
        value = raw.get('res', raw).get('result', '')
        if not isinstance(value, str):
            raise RuntimeError('代码增强返回了不支持的结果结构')
        with Image.open(image_path) as image:
            width,height = image.size
        poly = [[0,0],[width,0],[width,height],[0,height]]
        doc = new_document(width,height, engine='snnh/PaddleOCR-VL-code-v6', raw_result=raw,
                           device=device, inference_seconds=round(time.perf_counter()-start,3),
                           model_revision=MODEL_REVISION, coordinate_level='region')
        doc['blocks'] = [block(line, poly, kind='code', issues=['增强结果仅有区域坐标'], region=0) for line in value.split('\n')]
        doc['peak_gpu_bytes'] = int(paddle.device.cuda.max_memory_allocated()) if device.startswith('gpu') else None
        doc['settings'] = {'max_new_tokens':4096,'max_pixels':1280*1280,'prompt':'OCR:',
                           'decoding':'native greedy; repetition_penalty unsupported'}
        return doc

    @staticmethod
    def vl_config():
        from paddlex.inference import load_pipeline_config
        config = load_pipeline_config("PaddleOCR-VL-1.6")
        config["batch_size"] = 1
        config["SubModules"]["LayoutDetection"]["batch_size"] = 1
        config["SubModules"]["VLRecognition"]["batch_size"] = 1
        return config

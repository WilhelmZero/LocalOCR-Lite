"""Conservative code transcription: geometry is evidence, never syntax repair."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import statistics
from difflib import SequenceMatcher
from pathlib import Path

from .core import block, uid

LANGUAGES = {'text': '.txt', 'python': '.py', 'javascript': '.js', 'typescript': '.ts',
             'java': '.java', 'c': '.c', 'cpp': '.cpp', 'html': '.html', 'css': '.css',
             'sql': '.sql', 'json': '.json'}
DEFAULTS = dict(language='text', image_type='screenshot', indent=4, filter_watermark=True,
                watermark_text='', remove_line_numbers=True, regions=[], watermark_regions=[], corners=[])
MODEL_REVISION = 'bdf898bb154a7b1cdfb74ae6cab39bf7e13277e7'


def prepare_model(folder):
    """PaddleX discovery requires the canonical name, even for a one-shard HF model."""
    import os
    import shutil
    folder = Path(folder)
    target = folder/'model.safetensors'
    if target.exists():
        return
    index = json.loads((folder/'model.safetensors.index.json').read_text(encoding='utf-8'))
    shards = set(index['weight_map'].values())
    if len(shards)!=1:
        raise ValueError('当前适配器仅支持已验证的单分片代码模型')
    source = folder/next(iter(shards))
    if source.parent.resolve()!=folder.resolve() or not source.is_file():
        raise ValueError('代码模型下载不完整，请重新安装')
    try:
        os.link(source,target)
    except OSError:
        shutil.copy2(source,target)


def options(value=None):
    result = copy.deepcopy(DEFAULTS)
    result.update(value or {})
    if result['language'] not in LANGUAGES or result['image_type'] not in {'screenshot', 'photo'}:
        raise ValueError('无效的代码识别设置')
    if result['indent'] not in (2, 4):
        raise ValueError('缩进宽度必须为 2 或 4')
    for key in ('regions','watermark_regions'):
        if not isinstance(result[key],list) or any(not isinstance(r,list) or len(r)!=4 or
                not all(isinstance(x,(int,float)) and math.isfinite(x) for x in r) or r[2]<=r[0] or r[3]<=r[1] for r in result[key]):
            raise ValueError('区域必须为有效的 [左,上,右,下] 矩形')
    if result['corners'] and (not isinstance(result['corners'],list) or len(result['corners'])!=4 or
            any(not isinstance(p,list) or len(p)!=2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in p) for p in result['corners'])):
        raise ValueError('屏幕四角必须为四个 [x,y] 坐标')
    return result


def config_key(value):
    value=options(value)
    value.pop('source_fingerprint',None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def bounds(b):
    p = b.get('polygon', [])
    if not p:
        return (0, 0, 0, 0)
    xs, ys = zip(*p)
    return min(xs), min(ys), max(xs), max(ys)


def rect_poly(r):
    x, y, xx, yy = r
    return [[x, y], [xx, y], [xx, yy], [x, yy]]


def intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def columns(text):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def reconstruct(doc, cfg):
    cfg = options(cfg)
    original = copy.deepcopy(doc['blocks'])
    retained, excluded = [], []
    marks = cfg['watermark_regions'] if cfg['filter_watermark'] else []
    for b in original:
        b['issues'] = list(b.get('issues', []))
        r = bounds(b)
        p = b.get('polygon', [])
        angle = math.degrees(math.atan2(p[1][1]-p[0][1], p[1][0]-p[0][0])) if len(p) > 1 else 0
        peers = [x for x in original if x is not b and x['text'] == b['text']]
        known = bool(cfg['watermark_text'] and b['text'].strip() == cfg['watermark_text'].strip())
        edge = r[1] < doc['height']*.08 or r[3] > doc['height']*.92
        aligned = any(abs(bounds(x)[0]-r[0]) < max(12,(r[3]-r[1])*.8) for x in original if x is not b and x['text']!=b['text'])
        # A keyword match alone is never sufficient. Overlapping text remains reviewable.
        isolated = not any(intersects(r, bounds(x)) for x in original if x is not b)
        manually_marked = any(m[0]<=r[0] and m[1]<=r[1] and m[2]>=r[2] and m[3]>=r[3] for m in marks)
        suspect = cfg['filter_watermark'] and ((abs(angle) > 15 and (known or len(peers) >= 2)) or ((known or manually_marked) and edge and not aligned))
        if suspect and isolated:
            excluded.append({**b, 'reason': '独立水印：方向 / 边缘位置与文字证据'})
            continue
        if suspect or any(intersects(r, mark) for mark in marks):
            b['issues'].append('水印重叠：请对照原图')
        retained.append(b)
    doc['filter_records'] = excluded
    doc['unfiltered_blocks'] = original
    regions = cfg['regions'] or [[0, 0, doc['width'], doc['height']]]
    result = []
    for region_index, region in enumerate(regions):
        group = []
        for b in retained:
            r = bounds(b)
            if region[0] <= (r[0]+r[2])/2 <= region[2] and region[1] <= (r[1]+r[3])/2 <= region[3]:
                group.append(copy.deepcopy(b))
        group.sort(key=lambda b: (bounds(b)[1]+bounds(b)[3])/2)
        rows = []
        for b in group:
            r = bounds(b)
            cy, height = (r[1]+r[3])/2, max(1, r[3]-r[1])
            if rows and abs(cy-rows[-1][0]) < height*.45:
                rows[-1][1].append(b)
            else:
                rows.append((cy, [b]))
        for _, row in rows:
            row.sort(key=lambda b: bounds(b)[0])
        # Only a separate, aligned, consecutive gutter is removed.
        numbered = [(i, row[0]) for i, (_, row) in enumerate(rows) if row and re.fullmatch(r'\d+', row[0]['text'].strip())]
        if cfg['remove_line_numbers'] and len(numbered) >= 3:
            nums = [int(b['text']) for _, b in numbered]
            xs = [bounds(b)[0] for _, b in numbered]
            has_code = sum(len(rows[i][1])>1 for i,_ in numbered)>=3
            separate = all(len(rows[i][1])<2 or bounds(rows[i][1][1])[0]-bounds(b)[2]>10 for i,b in numbered)
            if has_code and separate and all(y == x+1 for x, y in zip(nums, nums[1:])) and max(xs)-min(xs) < 12:
                for i, b in numbered:
                    rows[i][1].remove(b)
                    excluded.append({**b, 'reason': '连续行号栏'})
        segments = [b for _, row in rows for b in row]
        if not segments:
            continue
        pitches = [(bounds(b)[2]-bounds(b)[0])/columns(b['text'].strip()) for b in segments if columns(b['text'].strip()) >= 5]
        pitch = statistics.median(pitches) if pitches else max(1, statistics.median(bounds(b)[3]-bounds(b)[1] for b in segments)*.55)
        pitch = max(1, pitch)
        left = min(bounds(b)[0] for b in segments)
        gaps = [b[0]-a[0] for a, b in zip(rows, rows[1:]) if b[0]>a[0]]
        spacing = statistics.median(gaps) if gaps else 0
        previous_y = None
        if result:
            result.append(block('', kind='code', region=region_index, issues=[]))
        for cy, row in rows:
            if not row:
                result.append(block('',kind='code',region=region_index,issues=[]))
                previous_y = cy
                continue
            if previous_y is not None and spacing and cy-previous_y > spacing*1.65:
                for _ in range(min(20, max(0, round((cy-previous_y)/spacing)-1))):
                    result.append(block('', kind='code', region=region_index, issues=['空行由图像间距推断']))
            previous_y = cy
            first = bounds(row[0])
            indent = max(0, round((first[0]-left)/pitch))
            text = ' '*indent
            last_right = None
            for b in row:
                r = bounds(b)
                if last_right is not None:
                    text += ' '*max(1, round((r[0]-last_right)/pitch))
                text += b['text'].strip()
                last_right = r[2]
            issues = list(dict.fromkeys(x for b in row for x in b['issues']))
            if indent and (indent % cfg['indent'] or not pitches):
                issues.append('缩进宽度不确定')
            rs = [bounds(b) for b in row]
            scores = [b['confidence'] for b in row if b.get('confidence') is not None]
            result.append(block(text, rect_poly([min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs)]),
                                min(scores) if len(scores)==len(row) else None, kind='code',
                                region=region_index, issues=issues, source_ids=[b['id'] for b in row]))
    doc.update(blocks=result, mode='code', code_options=cfg, config_key=config_key(cfg), code_regions=regions)
    return doc


def edit_code(doc, text):
    """Retain exact-line associations; edited one-to-one lines keep region provenance."""
    old = doc['blocks']
    old_text = [b['text'] for b in old]
    lines = text.split('\n')
    result = []
    for tag, a, b, c, d in SequenceMatcher(None, old_text, lines, autojunk=False).get_opcodes():
        if tag == 'equal':
            result.extend(copy.deepcopy(old[a:b]))
        elif tag in ('replace', 'insert'):
            for offset, line in enumerate(lines[c:d]):
                value = copy.deepcopy(old[a+offset]) if tag == 'replace' and b-a == d-c else block(kind='code', issues=[])
                value.update(text=line, manually_edited=True)
                result.append(value)
    doc['blocks'] = result


def accept_region(doc, candidate):
    """ROI coordinates are translated only when no extra rotation occurred in the crop."""
    left,top,_,_ = candidate['roi']
    values = copy.deepcopy(candidate['blocks'])
    for b in values:
        b['id'] = uid()
        b['polygon'] = [[x+left,y+top] for x,y in b.get('polygon',[])]
        b['manually_accepted'] = True
    index = next((i for i,b in enumerate(doc['blocks']) if b['id']==candidate.get('target_id')),len(doc['blocks']))
    doc['blocks'][index:index+1] = values


def correct_image(image, cfg):
    import cv2
    import numpy as np
    from PIL import Image
    array = np.array(image)
    transforms = []
    if cfg['corners']:
        pts = np.array(cfg['corners'], dtype=np.float32)
        if pts.shape != (4, 2) or not cv2.isContourConvex(pts.astype(np.int32)):
            raise ValueError('四角应按左上、右上、右下、左下顺序组成凸四边形')
        width = int(max(np.linalg.norm(pts[1]-pts[0]), np.linalg.norm(pts[2]-pts[3])))
        height = int(max(np.linalg.norm(pts[3]-pts[0]), np.linalg.norm(pts[2]-pts[1])))
        if min(width, height) < 20 or max(width, height) > 16000:
            raise ValueError('屏幕四角范围无效')
        matrix = cv2.getPerspectiveTransform(pts, np.float32([[0,0],[width-1,0],[width-1,height-1],[0,height-1]]))
        array = cv2.warpPerspective(array, matrix, (width, height), borderValue=(255,255,255))
        transforms.append({'type': 'perspective', 'matrix': matrix.tolist(), 'inverse': np.linalg.inv(matrix).tolist()})
    elif cfg['image_type'] == 'photo':
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        lines = cv2.HoughLinesP(cv2.Canny(gray, 50,150), 1, np.pi/1800, 80, minLineLength=max(60,image.width//6), maxLineGap=15)
        angles = [] if lines is None else [math.degrees(math.atan2(y2-y1,x2-x1)) for x1,y1,x2,y2 in lines[:,0] if x2!=x1]
        angles = [a for a in angles if abs(a)<12]
        if len(angles)>=3 and abs(statistics.median(angles))>=.3:
            angle = statistics.median(angles)
            h,w = array.shape[:2]
            matrix = cv2.getRotationMatrix2D((w/2,h/2),angle,1)
            nw,nh = math.ceil(h*abs(matrix[0,1])+w*abs(matrix[0,0])), math.ceil(h*abs(matrix[0,0])+w*abs(matrix[0,1]))
            matrix[:,2] += [(nw-w)/2,(nh-h)/2]
            array = cv2.warpAffine(array,matrix,(nw,nh),borderValue=tuple(int(x) for x in np.median(array.reshape(-1,3),axis=0)))
            transforms.append({'type':'deskew','matrix':matrix.tolist(),'inverse':cv2.invertAffineTransform(matrix).tolist()})
    return Image.fromarray(array), transforms


def suppress_watermark(image, rectangles):
    """Alternative view only: local contrast suppresses faint overlays, no inpainting."""
    import cv2
    import numpy as np
    from PIL import Image
    array = np.array(image)
    for rect in rectangles:
        x,y,xx,yy = map(int,rect)
        x,y,xx,yy = max(0,x),max(0,y),min(image.width,xx),min(image.height,yy)
        if xx<=x or yy<=y:
            continue
        part = array[y:yy,x:xx]
        gray = cv2.cvtColor(part,cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        array[y:yy,x:xx] = cv2.cvtColor(binary,cv2.COLOR_GRAY2RGB)
    return Image.fromarray(array)

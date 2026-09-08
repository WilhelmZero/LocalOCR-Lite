"""Persistent JSON-lines subprocess. No Qt imports or UI-thread model work."""
from __future__ import annotations

import contextlib
import json
import sys
import time
import traceback
from pathlib import Path

from .core import file_hash, read_page, uid
from .engines import Engine, configure_environment


def prepare(job):
    from PIL import ImageEnhance
    source = Path(job.get("view_source") or job["path"])
    image = read_page(source, 0 if job.get("view_source") else job["page"])
    transform = []
    from .watermark import settings as color_settings, remove_color
    color_cfg=color_settings(job.get('color_watermark'))
    if color_cfg['enabled'] and not job.get('skip_color_watermark'):
        directory=Path(job['data_dir'])/'previews'
        directory.mkdir(parents=True,exist_ok=True)
        prefix=uid()
        before=directory/(prefix+'-before-color.png')
        cleaned=directory/(prefix+'-clean-color.png')
        mask_path=directory/(prefix+'-color-mask.png')
        image.save(before)
        image,mask,stats=remove_color(image,color_cfg)
        image.save(cleaned);mask.save(mask_path)
        transform.append({'type':'color_watermark','original_path':str(before),'cleaned_path':str(cleaned),'mask_path':str(mask_path),**stats})
    if job['mode'] == 'code' and not job.get('view_source'):
        from .codeocr import correct_image, options
        image, spatial = correct_image(image, options(job.get('code_options')))
        transform.extend(spatial)
    if job.get("roi"):
        left, top, right, bottom = job["roi"]
        left, top, right, bottom = max(0, int(left)), max(0, int(top)), min(image.width, int(right)), min(image.height, int(bottom))
        if right-left < 3 or bottom-top < 3:
            raise ValueError("框选区域太小")
        image = image.crop((left, top, right, bottom))
        transform.append({"type": "crop", "box": [left, top, right, bottom]})
    if job["mode"] == "photo" and not job.get("enhance"):
        import cv2
        import numpy as np
        array = np.array(image)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi/1800, threshold=100,
                                minLineLength=max(80, image.width//5), maxLineGap=20)
        angles = []
        if lines is not None:
            for x1, y1, x2, y2 in lines[:, 0]:
                angle = float(np.degrees(np.arctan2(y2-y1, x2-x1)))
                if abs(angle) < 12:
                    angles.append(angle)
        if len(angles) >= 3:
            angle = float(np.median(angles))
            if abs(angle) >= 0.3:
                image = image.rotate(angle, expand=True, fillcolor="white", resample=3)
                transform.append({"type": "deskew", "angle": angle, "expand": True})
        image = ImageEnhance.Contrast(image).enhance(1.12)
    destination = Path(job["data_dir"]) / "previews" / (uid() + ".png")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return destination, transform


def execute(engine, job):
    if file_hash(job["path"]) != job["fingerprint"]:
        raise ValueError("来源图片已改变，请刷新文件夹后重新识别")
    image, transform = prepare(job)
    device = job.get("device", "auto")
    if job['mode']=='code' and job.get('enhance'):
        return attach_color(execute_code_enhanced(engine,job,image,transform,device),job,transform)
    try:
        doc = engine.run(image, job["mode"], device, job["data_dir"], job.get("enhance", False))
    except Exception as exc:
        message = str(exc).lower()
        if any(word in message for word in ("out of memory", "resourceexhausted", "cudaerror", "cuda error")):
            engine.release()
            try:
                doc = engine.run(image, job["mode"], device, job["data_dir"], job.get("enhance", False))
            except Exception as retry:
                raise RuntimeError("GPU 重试仍失败，请使用「CPU 重试」。" + str(retry)) from retry
        else:
            raise
    if file_hash(job["path"]) != job["fingerprint"]:
        raise ValueError("识别期间来源图片发生变化，结果未提交，请刷新")
    doc.setdefault("view_path", str(image))
    if job['mode'] == 'code':
        from .codeocr import options, reconstruct, config_key, suppress_watermark, intersects, bounds
        from PIL import Image
        cfg = options(job.get('code_options'))
        if job.get('enhance'):
            doc.update(code_options=cfg, config_key=config_key(cfg), code_regions=cfg['regions'])
        else:
            doc = reconstruct(doc, cfg)
        marks = cfg['watermark_regions'] if cfg['filter_watermark'] else []
        if marks and not job.get('enhance'):
            processed = Path(job['data_dir']) / 'previews' / (uid()+'-watermark.png')
            with Image.open(image) as source:
                suppress_watermark(source.convert('RGB'), marks).save(processed)
            alternative = reconstruct(engine.run(processed, 'code', device, job['data_dir']), cfg)
            doc['watermark_candidate'] = alternative
            doc['watermark_view'] = str(processed)
            for b in doc['blocks']:
                if any(intersects(bounds(b), r) for r in marks):
                    matches = [a for a in alternative['blocks'] if intersects(bounds(a), bounds(b))]
                    if not any(a['text'] == b['text'] for a in matches):
                        b.setdefault('issues', []).append('原图与水印处理图识别不一致')
    doc["source_preview"] = str(image)
    doc["transform"] = transform + doc.get("transform", [])
    if transform:
        doc["coordinate_space"] = "corrected"
    doc.update(mode=job["mode"], fingerprint=job["fingerprint"], roi=job.get("roi"),
               source_path=job["path"], page=job["page"], enhanced=job.get("enhance", False))
    return attach_color(doc,job,transform)


def attach_color(doc,job,transform):
    from .watermark import settings,profile_key
    cfg=settings(job.get('color_watermark'))
    doc['color_watermark_config']=cfg
    doc['color_watermark_key']=profile_key(cfg)
    if job.get('skip_color_watermark'):
        doc['color_watermark_skipped']='parent view already prepared'
    info=next((t for t in transform if t['type']=='color_watermark'),None)
    if info:
        doc['color_watermark']=info
    return doc


def execute_code_enhanced(engine, job, image_path, transform, device):
    import copy
    import numpy as np
    from PIL import Image, ImageDraw
    from .codeocr import options, reconstruct, config_key, bounds, rect_poly, intersects
    from .core import new_document
    cfg=options(job.get('code_options'))
    start=time.perf_counter()
    excluded=[]
    raw_baseline=None
    with Image.open(image_path) as source:
        image=source.convert('RGB')
    if cfg['filter_watermark']:
        baseline=reconstruct(engine.run(image_path,'code',device,job['data_dir']),cfg)
        raw_baseline=baseline.get('raw_result')
        excluded=baseline.get('filter_records',[])
        # Only spatially isolated watermark detections are masked. Line gutters stay intact.
        for entry in excluded:
            if '水印' in entry.get('reason',''):
                x,y,xx,yy=map(int,bounds(entry))
                arr=np.array(image)
                background=tuple(int(c) for c in np.median(arr.reshape(-1,3),axis=0))
                ImageDraw.Draw(image).rectangle((x,y,xx,yy),fill=background)
    regions=cfg['regions'] or [[0,0,image.width,image.height]]
    doc=new_document(image.width,image.height,mode='code',code_options=cfg,config_key=config_key(cfg),
                     code_regions=regions,filter_records=excluded,raw_detection=raw_baseline,coordinate_level='region')
    raw=[]
    input_views=[]
    for index,region in enumerate(regions):
        left,top,right,bottom=map(int,region)
        left,top,right,bottom=max(0,left),max(0,top),min(image.width,right),min(image.height,bottom)
        if right-left<3 or bottom-top<3:
            raise ValueError('代码区域无效，请重新框选')
        cropped=Path(job['data_dir'])/'previews'/(uid()+'-code-region.png')
        image.crop((left,top,right,bottom)).save(cropped)
        input_views.append(str(cropped))
        try:
            result=engine.run(cropped,'code',device,job['data_dir'],True)
        except Exception as exc:
            if any(word in str(exc).lower() for word in ('out of memory','resourceexhausted','cudaerror','cuda error')):
                engine.release()
                try:
                    result=engine.run(cropped,'code',device,job['data_dir'],True)
                except Exception as retry:
                    raise RuntimeError('代码增强 GPU 重试失败，可点击 CPU 重试（速度较慢）。'+str(retry)) from retry
            else:
                raise
        raw.append(result['raw_result'])
        if doc['blocks']:
            from .core import block
            doc['blocks'].append(block('',kind='code',issues=[]))
        for b in result['blocks']:
            b['polygon']=rect_poly([left,top,right,bottom])
            b['region']=index
            if any(intersects(region,r) for r in cfg['watermark_regions']):
                b['issues'].append('本区域存在水印，请对照原图')
            doc['blocks'].append(b)
        for key in ('engine','device','model_revision','peak_gpu_bytes','settings'):
            doc[key]=result.get(key)
    if file_hash(job['path'])!=job['fingerprint']:
        raise ValueError('识别期间来源图片发生变化，结果未提交，请刷新')
    doc.update(raw_result=raw,enhancement_inputs=input_views,view_path=str(image_path),source_preview=str(image_path),transform=transform,
               coordinate_space='corrected' if transform else 'exif_normalized',fingerprint=job['fingerprint'],
               roi=job.get('roi'),source_path=job['path'],page=job['page'],enhanced=True,inference_seconds=round(time.perf_counter()-start,3))
    return doc


def main():
    output = sys.stdout
    configured = False
    engine = Engine()
    for line in sys.stdin:
        job = {}
        try:
            job = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if not configured:
                    configure_environment(job["data_dir"], job.get("offline", True))
                    configured = True
                start = time.perf_counter()
                doc = execute(engine, job)
                doc["total_seconds"] = round(time.perf_counter()-start, 3)
            result = {"job_id": job["job_id"], "result": doc}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            result = {"job_id": job.get("job_id"), "error": str(exc)}
        output.write("@@OCR@@" + json.dumps(result, ensure_ascii=False) + "\n")
        output.flush()


if __name__ == "__main__":
    main()

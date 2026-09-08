"""Local color masking and image cleanup. No source-image writes or OCR inference."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

DEFAULTS = dict(enabled=False, colors=[], tolerance=12.0, method='background',
                background=None, expand=0, protect_text=True, blend_edges=True)


def settings(value=None):
    result=copy.deepcopy(DEFAULTS)
    result.update({k:v for k,v in (value or {}).items() if k in DEFAULTS})
    def color(c):
        return isinstance(c,list) and len(c)==3 and all(type(v)==int and 0<=v<=255 for v in c)
    if not isinstance(result['colors'],list) or len(result['colors'])>16 or not all(color(c) for c in result['colors']):
        raise ValueError('请选择最多 16 个有效 RGB 水印颜色')
    if result['background'] is not None and not color(result['background']):
        raise ValueError('背景色必须为 RGB 颜色')
    if not isinstance(result['tolerance'],(int,float)) or not math.isfinite(result['tolerance']) or not 0<=result['tolerance']<=60:
        raise ValueError('颜色容差必须在 0–60 之间')
    if result['method'] not in ('background','inpaint') or type(result['expand'])!=int or not 0<=result['expand']<=3:
        raise ValueError('无效的水印处理方式或扩展半径')
    result['enabled']=bool(result['enabled'])
    result['protect_text']=bool(result['protect_text'])
    result['blend_edges']=bool(result['blend_edges'])
    return result


def profile_key(value):
    cfg=settings(value)
    if not cfg['enabled']:
        cfg={'enabled':False}
    return hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()


def background_color(array):
    """Estimate each image separately, so one profile can handle light/dark screens."""
    import numpy as np
    sample=array[::max(1,array.shape[0]//400),::max(1,array.shape[1]//400)].reshape(-1,3)
    bins=sample.astype('int32')//16
    keys=bins[:,0]*256+bins[:,1]*16+bins[:,2]
    winner=np.bincount(keys,minlength=4096).argmax()
    return np.median(sample[keys==winner],axis=0).astype('uint8')


def remove_color(image, value):
    import cv2
    import numpy as np
    from PIL import Image
    cfg=settings(value)
    array=np.array(image.convert('RGB'))
    mask=np.zeros(array.shape[:2],dtype='uint8')
    if not cfg['enabled']:
        return Image.fromarray(array),Image.fromarray(mask),{'matched_pixels':0,'fraction':0.0,'applied':False}
    if not cfg['colors']:
        raise ValueError('颜色去水印已启用，但尚未选择水印颜色')
    estimated=background_color(array)
    if cfg['method']=='background' and cfg['background'] is not None:
        pair=cv2.cvtColor(np.float32([[cfg['background'],estimated.tolist()]])/255.0,cv2.COLOR_RGB2Lab)[0]
        if float(np.linalg.norm(pair[0]-pair[1]))>25:
            raise ValueError('指定替换色与图片背景差异过大，会把水印染色而不是去除。请开启「每张图片自动估计背景色」，或对复杂背景使用周围像素修补。')
    samples=cv2.cvtColor(np.float32([cfg['colors']])/255.0,cv2.COLOR_RGB2Lab)[0]
    # Process Lab distances in strips to bound temporary memory for large photographs.
    for top in range(0,array.shape[0],256):
        lab=cv2.cvtColor(array[top:top+256].astype('float32')/255.0,cv2.COLOR_RGB2Lab)
        selected=np.zeros(lab.shape[:2],dtype=bool)
        for color in samples:
            selected|=np.sum((lab-color)**2,axis=2)<=cfg['tolerance']**2
        mask[top:top+256]=selected.astype('uint8')*255
    if cfg['blend_edges'] and cfg['method']=='background':
        # Antialiased stamp borders blend towards the background and fall outside
        # a single-color tolerance. Include only nearby pixels on that blend path.
        near=cv2.dilate(mask,np.ones((5,5),dtype='uint8'))!=0
        bg_lab=cv2.cvtColor(np.float32([[estimated.tolist()]])/255.0,cv2.COLOR_RGB2Lab)[0,0]
        for top in range(0,array.shape[0],256):
            lab=cv2.cvtColor(array[top:top+256].astype('float32')/255.0,cv2.COLOR_RGB2Lab)
            edge=np.zeros(lab.shape[:2],dtype=bool)
            for color in samples:
                direction=bg_lab-color
                length=float(np.dot(direction,direction))
                if length<1:continue
                t=np.sum((lab-color)*direction,axis=2)/length
                distance=np.sum((lab-(color+t[...,None]*direction))**2,axis=2)
                edge|=(t>=0)&(t<=1)&(distance<=max(2,cfg['tolerance'])**2)
            mask[top:top+256][edge & near[top:top+256]]=255
    if cfg['expand']:
        size=2*cfg['expand']+1
        mask=cv2.dilate(mask,np.ones((size,size),dtype='uint8'))
    background=np.array(cfg['background'],dtype='uint8') if cfg['background'] is not None else estimated
    if cfg['protect_text']:
        gray=cv2.cvtColor(array,cv2.COLOR_RGB2GRAY)
        background_luma=float(np.dot(background,[.299,.587,.114]))
        mask[(gray<80) if background_luma>=128 else (gray>180)]=0
    count=int(np.count_nonzero(mask))
    fraction=count/mask.size
    if count and cfg['method']=='inpaint':
        if fraction>.25:
            raise ValueError('匹配范围超过图片的 25%，不适合局部修补；请降低容差或改用背景色替换')
        cleaned=cv2.inpaint(array,mask,3,cv2.INPAINT_TELEA)
    else:
        cleaned=array.copy()
        cleaned[mask!=0]=background
    return Image.fromarray(cleaned),Image.fromarray(mask),{
        'matched_pixels':count,'fraction':fraction,'applied':True,
        'changed_pixels':int(np.count_nonzero(np.any(cleaned!=array,axis=2))),
        'background':background.tolist(),'color_space':'CIE Lab / Delta E 76',
        'method':cfg['method'],'geometry':'identity'}


def export_cleaned(store,root,destination):
    """Export newest full-page cleanup per current source, including unadopted OCR candidates."""
    import shutil
    destination=Path(destination)
    exported=[]
    for page in store.pages(root):
        for version in store.versions(page['id']):
            doc=store.version_document(version['id'],original=True)
            info=doc.get('color_watermark',{})
            if doc.get('roi') or doc.get('fingerprint')!=page['fingerprint'] or not info.get('cleaned_path'):
                continue
            path=Path(info['cleaned_path'])
            if not path.exists():
                continue
            relative=Path(page['relative'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('无效的导出相对路径')
            target=destination/relative.parent/f"{relative.name}.page-{page['page']+1:04d}.clean.png"
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)
            exported.append({'image':page['relative'],'page':page['page'],'output':str(target.relative_to(destination)),
                             'profile':doc.get('color_watermark_config'),'stats':info})
            break
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'去水印记录.json').write_text(json.dumps(exported,ensure_ascii=False,indent=2),encoding='utf-8')
    return len(exported)

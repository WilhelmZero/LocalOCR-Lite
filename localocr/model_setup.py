"""Explicit model installation in a separate process; never reads user images."""
import argparse
import importlib.metadata as metadata
import json
import os
import platform
import sys
import time
from pathlib import Path

FEATURES={'print':'基础文字 / 代码','photo':'拍照方向校正','structure':'表格 / 复杂排版',
          'vl':'文档 VL 增强','code-vl':'代码 VL 增强'}

def installation_commands(feature,device='cpu'):
    if feature not in FEATURES:raise ValueError('Unknown feature')
    if sys.platform=='darwin':
        if platform.machine()!='arm64':raise ValueError('当前 Paddle 3.3.1 没有 Intel Mac 安装包；此版本 OCR 仅适配 Apple Silicon。')
        if device!='cpu':raise ValueError('macOS 此版本仅提供 CPU 推理。')
        if feature in ('vl','code-vl'):raise ValueError('Mac VL 增强尚未完成适配验证，暂不开放下载。')
    packages=['paddleocr[doc-parser]==3.7.0' if feature in ('structure','vl','code-vl') else 'paddleocr==3.7.0',
              'paddlex==3.7.2']
    # Switching between the CPU/GPU wheels is explicit, not done during OCR.
    commands=[]
    target='paddlepaddle-gpu' if device=='gpu' else 'paddlepaddle'
    opposite='paddlepaddle' if device=='gpu' else 'paddlepaddle-gpu'
    try:metadata.version(opposite)
    except metadata.PackageNotFoundError:pass
    else:commands.append(['-m','pip','uninstall','-y',opposite])
    commands.append(['-m','pip','install','-r',str(Path(__file__).resolve().parents[1]/'requirements-ui.lock.txt'),*packages])
    commands.append(['-m','pip','install',target+'==3.3.1',*(['-i','https://www.paddlepaddle.org.cn/packages/stable/cu126/'] if device=='gpu' else [])])
    commands.append(['-m','pip','check'])
    commands.append(['-u','-m','localocr.model_setup',feature,'--device',device])
    return commands

def main():
    parser=argparse.ArgumentParser();parser.add_argument('feature',choices=FEATURES);parser.add_argument('--device',choices=['cpu','gpu'],default='cpu')
    args=parser.parse_args()
    from .core import app_data
    from .engines import Engine,configure_environment
    from PIL import Image
    import tempfile
    directory=app_data();configure_environment(directory,False)
    if args.feature=='code-vl':
        import runpy
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'tools/install_code_model.py'),run_name='__main__')
    engine=Engine()
    try:
        with tempfile.TemporaryDirectory(prefix='ocr-model-check-') as temp:
            path=Path(temp)/'blank.png';Image.new('RGB',(640,480),'white').save(path)
            mode='code' if args.feature=='code-vl' else 'print' if args.feature=='vl' else args.feature
            engine.run(path,mode,'gpu:0' if args.device=='gpu' else 'cpu',directory,enhance=args.feature in ('vl','code-vl'))
    finally:engine.release()
    versions={}
    for package in ('paddleocr','paddlex','paddlepaddle','paddlepaddle-gpu'):
        try:versions[package]=metadata.version(package)
        except metadata.PackageNotFoundError:pass
    record={'feature':args.feature,'device':args.device,'platform':platform.platform(),'machine':platform.machine(),'packages':versions,'verified_at':time.time()}
    (directory/('model-'+args.feature+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print('模型已下载并完成本机空白页推理验证；可以关闭允许下载模型，离线识别。',flush=True)

if __name__=='__main__':main()

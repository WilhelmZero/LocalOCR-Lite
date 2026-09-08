"""Download only pinned data assets; model code uses the installed PaddleX runtime."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from huggingface_hub import snapshot_download
from localocr.core import app_data
from localocr.codeocr import MODEL_REVISION, prepare_model

if __name__ == '__main__':
    snapshot_download('snnh/paddleocr_vl_code_ocr', revision=MODEL_REVISION,
                      local_dir=app_data()/'models/code-vl',
                      allow_patterns=['*.json','*.safetensors','*.model','*.jinja','*.yml','README.md'])
    prepare_model(app_data()/'models/code-vl')
    import json
    from localocr.core import file_hash
    manifest=Path(__file__).resolve().parents[1]/'models.lock.json'
    if manifest.exists():
        expected=json.loads(manifest.read_text(encoding='utf-8'))['code_vl']['weights_sha256']
        actual=file_hash(app_data()/'models/code-vl/model.safetensors')
        if actual!=expected:
            raise RuntimeError('模型权重校验失败，请重新下载')
    print('代码增强模型下载完成；使用 --mode code --enhance 验证设备兼容性。')

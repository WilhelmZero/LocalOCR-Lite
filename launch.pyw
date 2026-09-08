import os
import sys
import traceback
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
os.environ.setdefault("LOCAL_OCR_DATA", str(root / "data"))
os.environ.setdefault("LOCAL_OCR_PYTHON", str(Path(sys.executable).with_name("python.exe")) if sys.platform=='win32' else sys.executable)

if __name__ == "__main__":
    try:
        from localocr.app import main
        main()
    except Exception:
        directory = root / "data" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "启动失败，详情见 data/logs/startup-error.log", "识文", 16)
        else:
            print(traceback.format_exc(),file=sys.stderr)

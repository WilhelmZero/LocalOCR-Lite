"""Entry point for the self-contained Apple Silicon application."""
import os
import sys
import traceback
from pathlib import Path


def main():
    data = Path.home() / "Library" / "Application Support" / "LocalOCR"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LOCAL_OCR_DATA", str(data))
    os.environ.setdefault("LOCAL_OCR_MODELS", str(data / "models"))
    # Child OCR and verification processes relaunch this same frozen executable.
    os.environ.setdefault("LOCAL_OCR_PYTHON", sys.executable)
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
            import platform
            import PySide6
            import cv2
            import paddle
            import paddleocr
            import paddlex
            from localocr.app import MainWindow
            if platform.machine() != "arm64":
                raise RuntimeError("The application must run as native Apple Silicon arm64")
            print("Apple Silicon application imports OK", flush=True)
            return
        if len(sys.argv) > 1 and sys.argv[1] == "--worker":
            from localocr.worker import main as run
            sys.argv.pop(1)
        elif len(sys.argv) > 1 and sys.argv[1] == "--model-setup":
            from localocr.model_setup import main as run
            sys.argv.pop(1)
        else:
            from localocr.app import main as run
        run()
    except Exception:
        log_dir = data / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()

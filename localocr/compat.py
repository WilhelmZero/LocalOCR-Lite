"""Scoped Windows workaround for Paddle's narrow-character model file reader."""
import contextlib
import os
import threading
from pathlib import Path

_lock = threading.RLock()


def install_paddle_unicode_fix():
    if os.name != "nt":
        return
    try:
        import sentencepiece
        processor = sentencepiece.SentencePieceProcessor
    except ImportError:
        processor = None  # Basic OCR does not need the optional VL tokenizer.
    if processor is not None and not getattr(processor, "_localocr_unicode_fix", False):
        original_load = processor.LoadFromFile
        def load(self, filename):
            if isinstance(filename, (str, os.PathLike)) and not str(filename).isascii() and Path(filename).is_file():
                return self.LoadFromSerializedProto(Path(filename).read_bytes())
            return original_load(self, filename)
        processor.LoadFromFile = load
        processor._localocr_unicode_fix = True
    from paddlex.inference.models.runners.paddle_static import runner
    cls = runner.PaddleStaticRunner
    if getattr(cls, "_localocr_unicode_fix", False):
        return
    original_create = cls._create
    original_paths = runner.get_model_paths

    def relative_paths(*args, **kwargs):
        paths = original_paths(*args, **kwargs)
        if "paddle" in paths:
            paths["paddle"] = tuple(Path(os.path.relpath(p, Path.cwd())) for p in paths["paddle"])
        return paths

    def create(self):
        # Construction is serialized, and the worker never handles UI/file tasks concurrently.
        # Windows accepts Unicode chdir; Paddle then opens ASCII model basenames.
        with _lock:
            previous = Path.cwd()
            model_dir = Path(self.model_dir).resolve()
            try:
                os.chdir(model_dir)
                runner.get_model_paths = relative_paths
                return original_create(self)
            finally:
                runner.get_model_paths = original_paths
                os.chdir(previous)

    cls._create = create
    cls._localocr_unicode_fix = True

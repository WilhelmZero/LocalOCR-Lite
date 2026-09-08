"""JSON document contract and durable, versioned review storage."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps

MODES = {"print": "印刷文档", "photo": "手写 / 拍照", "structure": "表格 / 复杂排版", "code": "代码识别"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
CONFIG_VERSION = "1"


def app_data() -> Path:
    path = Path(os.environ.get("LOCAL_OCR_DATA", Path(__file__).resolve().parent.parent / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def uid():
    return uuid.uuid4().hex


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_folder(root: str, stop=lambda: False):
    """Read-only snapshot; no symlink traversal and no generated output scanning."""
    root_path = Path(root).resolve()
    own_data = Path(os.environ.get("LOCAL_OCR_DATA", Path(__file__).resolve().parent.parent / "data")).resolve()
    for directory, dirs, files in os.walk(root_path, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "ocr-export"
                         and not Path(directory, d).is_symlink() and Path(directory, d).resolve() != own_data)
        for name in sorted(files, key=str.casefold):
            if stop():
                return
            path = Path(directory, name)
            if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                continue
            rel = path.relative_to(root_path).as_posix()
            try:
                before = path.stat()
                fingerprint = file_hash(path)
                with Image.open(path) as image:
                    count = getattr(image, "n_frames", 1) if path.suffix.lower() in {".tif", ".tiff"} else 1
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise OSError("扫描期间文件发生变化，请刷新")
                for page in range(count):
                    yield {"relative": rel, "fingerprint": fingerprint, "page": page, "error": ""}
            except Exception as exc:
                yield {"relative": rel, "fingerprint": "unreadable", "page": 0, "error": str(exc)}


def read_page(path, page=0):
    with Image.open(path) as source:
        source.seek(page)
        image = ImageOps.exif_transpose(source)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            image = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
        else:
            image = image.convert("RGB")
    return image


def block(text="", polygon=None, confidence=None, kind="text", **kwargs):
    return {"id": uid(), "kind": kind, "text": text, "polygon": polygon or [],
            "confidence": confidence, **kwargs}


def new_document(width=0, height=0, **kwargs):
    return {"schema_version": 1, "width": width, "height": height, "blocks": [],
            "tables": [], "coordinate_space": "exif_normalized", "transform": [], **kwargs}


def table_shape(table):
    cells = table.get("cells", [])
    return (max([c["row"] + c.get("rowspan", 1) for c in cells] + [table.get("rows", 0)]),
            max([c["col"] + c.get("colspan", 1) for c in cells] + [table.get("cols", 0)]))


def fill_table(table):
    rows, cols = table_shape(table)
    occupied = {(r, c) for cell in table["cells"]
                for r in range(cell["row"], cell["row"] + cell.get("rowspan", 1))
                for c in range(cell["col"], cell["col"] + cell.get("colspan", 1))}
    for r in range(rows):
        for c in range(cols):
            if (r, c) not in occupied:
                table["cells"].append({"row": r, "col": c, "rowspan": 1, "colspan": 1,
                                       "text": "", "polygon": [], "confidence": None})
    table["rows"], table["cols"] = rows, cols


def resize_table(table, axis, index, delete=False):
    """Insert/delete through merged cells while retaining a valid rectangular grid."""
    span_key = "rowspan" if axis == "row" else "colspan"
    size_key = "rows" if axis == "row" else "cols"
    fill_table(table)
    size = table[size_key]
    if delete and size <= 1:
        return
    kept = []
    for cell in table["cells"]:
        start, span = cell[axis], cell.get(span_key, 1)
        if delete:
            if start <= index < start + span:
                if span == 1:
                    continue
                cell[span_key] = span - 1
            elif start > index:
                cell[axis] -= 1
        else:
            if start >= index:
                cell[axis] += 1
            elif start < index < start + span:
                cell[span_key] = span + 1
        kept.append(cell)
    table["cells"] = kept
    table[size_key] = size + (-1 if delete else 1)
    fill_table(table)


def merge_cells(table, top, left, bottom, right):
    selected = []
    for cell in table["cells"]:
        r, c = cell["row"], cell["col"]
        re, ce = r + cell.get("rowspan", 1) - 1, c + cell.get("colspan", 1) - 1
        intersects = r <= bottom and re >= top and c <= right and ce >= left
        if intersects:
            if r < top or c < left or re > bottom or ce > right:
                raise ValueError("选择范围跨越已有合并单元格，请先拆分或完整选中")
            selected.append(cell)
    text = "\n".join(c["text"] for c in sorted(selected, key=lambda x: (x["row"], x["col"])) if c["text"])
    points = [p for cell in selected for p in cell.get("polygon", [])]
    poly = []
    if points:
        xs, ys = zip(*points)
        poly = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
    table["cells"] = [c for c in table["cells"] if c not in selected]
    table["cells"].append({"row": top, "col": left, "rowspan": bottom-top+1,
                           "colspan": right-left+1, "text": text, "polygon": poly, "confidence": None})


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS pages (
          id INTEGER PRIMARY KEY, root TEXT NOT NULL, relative TEXT NOT NULL, page INTEGER NOT NULL,
          fingerprint TEXT NOT NULL, mode TEXT NOT NULL, config TEXT NOT NULL DEFAULT '1',
          status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
          active_version INTEGER, reviewed INTEGER NOT NULL DEFAULT 0, present INTEGER NOT NULL DEFAULT 1,
          UNIQUE(root, relative, page));
        CREATE TABLE IF NOT EXISTS versions (
          id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(id),
          created REAL NOT NULL, fingerprint TEXT NOT NULL, mode TEXT NOT NULL,
          payload TEXT NOT NULL, candidate INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS revisions (
          id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id),
          created REAL NOT NULL, payload TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS requests (
          page_id INTEGER PRIMARY KEY REFERENCES pages(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS code_settings (
          page_id INTEGER PRIMARY KEY REFERENCES pages(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS watermark_profiles (
          root TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        self.db.execute("UPDATE pages SET status='pending' WHERE status='running'")
        self.db.commit()

    def sync(self, root, entries, mode):
        with self.db:
            self.db.execute("UPDATE pages SET present=0 WHERE root=?", (root,))
            for entry in entries:
                old = self.db.execute("SELECT * FROM pages WHERE root=? AND relative=? AND page=?",
                                      (root, entry["relative"], entry["page"])).fetchone()
                if not old:
                    self.db.execute("INSERT INTO pages(root,relative,page,fingerprint,mode,status,error) VALUES(?,?,?,?,?,?,?)",
                                    (root, entry["relative"], entry["page"], entry["fingerprint"], mode,
                                     "failed" if entry["error"] else "pending", entry["error"]))
                elif old["fingerprint"] != entry["fingerprint"]:
                    self.db.execute("UPDATE pages SET fingerprint=?,status=?,reviewed=0,error=?,present=1 WHERE id=?",
                                    (entry["fingerprint"], "changed" if old["active_version"] else "pending", entry["error"], old["id"]))
                else:
                    self.db.execute("UPDATE pages SET present=1 WHERE id=?", (old["id"],))

    def pages(self, root):
        return [dict(r) for r in self.db.execute("SELECT * FROM pages WHERE root=? AND present=1 ORDER BY relative COLLATE NOCASE,page", (root,))]

    def page(self, page_id):
        row = self.db.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        return dict(row) if row else None

    def status(self, page_id, status, error=""):
        with self.db:
            self.db.execute("UPDATE pages SET status=?,error=? WHERE id=?", (status, error, page_id))

    def set_mode(self, page_id, mode):
        with self.db:
            self.db.execute("UPDATE pages SET mode=? WHERE id=?", (mode, page_id))

    def save_request(self, page_id, job):
        with self.db:
            self.db.execute("INSERT INTO requests(page_id,payload) VALUES(?,?) ON CONFLICT(page_id) DO UPDATE SET payload=excluded.payload", (page_id, dumps(job)))

    def code_options(self, page_id):
        row = self.db.execute('SELECT payload FROM code_settings WHERE page_id=?', (page_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_code_options(self, page_id, cfg):
        cfg = {**cfg, 'source_fingerprint': self.page(page_id)['fingerprint']}
        with self.db:
            self.db.execute('INSERT INTO code_settings(page_id,payload) VALUES(?,?) ON CONFLICT(page_id) DO UPDATE SET payload=excluded.payload', (page_id,dumps(cfg)))

    def last_request(self, page_id):
        row = self.db.execute("SELECT payload FROM requests WHERE page_id=?", (page_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def watermark_settings(self,root):
        row=self.db.execute('SELECT payload FROM watermark_profiles WHERE root=?',(root,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_watermark_settings(self,root,cfg):
        with self.db:
            self.db.execute('INSERT INTO watermark_profiles(root,payload) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET payload=excluded.payload',(root,dumps(cfg)))

    def add_result(self, page_id, doc, candidate=False, replace_pristine=False):
        page = self.page(page_id)
        doc = copy.deepcopy(doc)
        doc.setdefault("fingerprint", page["fingerprint"])
        candidate = bool(candidate or page["active_version"])
        # Only full-page cleanup may replace an untouched machine draft. Check at
        # completion so edits made while OCR was running are protected too.
        pristine = page['active_version'] and not page['reviewed'] and not self.db.execute(
            'SELECT 1 FROM revisions WHERE version_id=? LIMIT 1', (page['active_version'],)).fetchone()
        if replace_pristine and pristine and not doc.get('roi') and doc.get('color_watermark') and doc.get('fingerprint')==page['fingerprint']:
            candidate = False
        with self.db:
            cursor = self.db.execute("INSERT INTO versions(page_id,created,fingerprint,mode,payload,candidate) VALUES(?,?,?,?,?,?)",
                                     (page_id, time.time(), doc.get("fingerprint", page["fingerprint"]),
                                      doc.get("mode", page["mode"]), dumps(doc), int(candidate)))
            version = cursor.lastrowid
            status = "done" if doc["blocks"] or doc["tables"] else "empty"
            if not candidate:
                self.db.execute("UPDATE pages SET active_version=?,reviewed=0 WHERE id=?", (version, page_id))
            elif page["active_version"] and self.document(page_id).get("fingerprint") != page["fingerprint"]:
                status = "changed"
            self.db.execute("UPDATE pages SET status=?,error='' WHERE id=?", (status, page_id))
        return version, candidate

    def document(self, page_id):
        page = self.page(page_id)
        if not page or not page["active_version"]:
            return None
        return self.version_document(page["active_version"])

    def version_document(self, version_id, original=False):
        revision = None if original else self.db.execute("SELECT payload FROM revisions WHERE version_id=? ORDER BY id DESC LIMIT 1", (version_id,)).fetchone()
        row = revision or self.db.execute("SELECT payload FROM versions WHERE id=?", (version_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_edit(self, page_id, doc, reviewed=False):
        page = self.page(page_id)
        version = page["active_version"]
        if not version:
            raise ValueError("请先创建识别结果")
        doc = copy.deepcopy(doc)
        doc.setdefault("fingerprint", self.version_document(version, True).get("fingerprint", page["fingerprint"]))
        stale = doc["fingerprint"] != page["fingerprint"]
        if stale and reviewed:
            raise ValueError("当前修订稿属于旧图片，请采纳新图片的识别结果后再核对")
        with self.db:
            self.db.execute("INSERT INTO revisions(version_id,created,payload,reviewed) VALUES(?,?,?,?)",
                            (version, time.time(), dumps(doc), int(reviewed)))
            self.db.execute("UPDATE pages SET reviewed=? WHERE id=?", (int(reviewed), page_id))
            if stale:
                self.db.execute("UPDATE pages SET status='changed' WHERE id=?", (page_id,))
            elif page["status"] == "changed":
                self.db.execute("UPDATE pages SET status='done' WHERE id=?", (page_id,))

    def undo(self, page_id):
        version = self.page(page_id)["active_version"]
        with self.db:
            self.db.execute("DELETE FROM revisions WHERE id=(SELECT MAX(id) FROM revisions WHERE version_id=?)", (version,))
            row = self.db.execute("SELECT reviewed FROM revisions WHERE version_id=? ORDER BY id DESC LIMIT 1", (version,)).fetchone()
            self.db.execute("UPDATE pages SET reviewed=? WHERE id=?", (row[0] if row else 0, page_id))
            doc = self.document(page_id)
            if doc and doc.get("fingerprint") != self.page(page_id)["fingerprint"]:
                self.db.execute("UPDATE pages SET status='changed',reviewed=0 WHERE id=?", (page_id,))
        return self.document(page_id)

    def versions(self, page_id):
        return [dict(r) for r in self.db.execute("SELECT id,created,mode,candidate,fingerprint FROM versions WHERE page_id=? ORDER BY id DESC", (page_id,))]

    def accept(self, page_id, version_id):
        doc = self.version_document(version_id, original=True)
        # Accept into a revision of the active version so Undo always restores manual work.
        self.save_edit(page_id, doc)
        with self.db:
            self.db.execute("UPDATE versions SET candidate=0 WHERE id=? AND page_id=?", (version_id, page_id))

    def close(self):
        self.db.close()

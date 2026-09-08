from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QLockFile
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QFontDatabase, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QFileDialog, QSplitter, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QTabWidget,
    QPlainTextEdit, QProgressBar, QCheckBox, QDoubleSpinBox, QMessageBox, QDialog,
    QDialogButtonBox, QInputDialog, QToolBar)

from .core import (Store, app_data, MODES, uid, block, new_document, read_page,
                   resize_table, merge_cells, fill_table)
from .exporting import export_project, render_text
from .jobs import Queue, Scanner
from .widgets import ImageView, TableGrid
from .codeui import CodeMixin
from .codeocr import options, config_key
from .watermark_ui import WatermarkMixin
from .watermark import settings as watermark_settings, profile_key as watermark_key

STATUS = {"pending": "待识别", "running": "识别中", "done": "待核对", "empty": "无文字",
          "failed": "识别失败", "changed": "来源已变化", "cancelled": "已取消"}

STYLE = """
QMainWindow, QDialog { background: #f4f7f6; }
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #203d35; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #164e3f; }
QLabel#muted { color: #70867e; }
QPushButton { background: #ffffff; border: 1px solid #d3dfda; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover { background: #e4f2ec; border-color: #83b39e; }
QPushButton:disabled { color: #a7b7b0; background: #eef2f0; }
QPushButton#primary { background: #197658; border: 1px solid #197658; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #126047; }
QComboBox, QDoubleSpinBox { background: white; border: 1px solid #d3dfda; border-radius: 5px; padding: 6px; }
QListWidget, QTableWidget, QPlainTextEdit { background: white; border: 1px solid #dce5e0; border-radius: 6px; selection-background-color: #d9efe4; selection-color: #143e2f; }
QListWidget::item { padding: 9px 7px; border-bottom: 1px solid #eff3f1; }
QListWidget::item:selected { background: #ddefe5; border-left: 3px solid #25805d; }
QHeaderView::section { background: #edf4f0; border: none; padding: 8px; font-weight: 600; }
QTabBar::tab { background: #edf3ef; padding: 9px 18px; border: 1px solid #dce5e0; }
QTabBar::tab:selected { background: white; color: #14704f; }
QProgressBar { background: #e4ece7; border: none; border-radius: 4px; text-align: center; height: 15px; }
QProgressBar::chunk { background: #67aa8b; border-radius: 4px; }
QSplitter::handle { background: #e1e9e4; }
QToolBar { border: none; spacing: 6px; background: #f4f7f6; }
"""


def button(text, callback, primary=False):
    result = QPushButton(text)
    result.clicked.connect(callback)
    if primary:
        result.setObjectName("primary")
    return result


def configure_app(app):
    app.setStyle("Fusion")
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    mono=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/consola.ttf'
    if mono.exists():
        QFontDatabase.addApplicationFont(str(mono))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)


class MainWindow(WatermarkMixin, CodeMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.data_dir = app_data()
        self.store = Store(self.data_dir / "projects.sqlite3")
        self.root, self.page_id, self.doc, self.view_path = "", None, None, None
        self.loading, self.scanner, self.candidates = False, None, []
        self.setWindowTitle("识文 · 本地 OCR 校对工作台 1.3.0")
        self.resize(1480, 920)
        self.setMinimumSize(1080, 700)
        self.queue = Queue(self.data_dir, self)
        self.queue.started.connect(self.job_started)
        self.queue.completed.connect(self.job_completed)
        self.queue.failed.connect(self.job_failed)
        self.queue.changed.connect(self.update_progress)
        self.queue.log.connect(self.append_log)
        self.build_ui()
        self.build_code_ui()
        self.build_watermark_ui()
        self.restore_settings()

    def build_ui(self):
        body = QWidget()
        self.setCentralWidget(body)
        main = QVBoxLayout(body)
        main.setContentsMargins(22, 18, 22, 12)
        header = QHBoxLayout()
        title = QLabel("识文  /  本地 OCR")
        title.setObjectName("brand")
        header.addWidget(title)
        subtitle = QLabel("批量识别 · 原图核对 · 本地保存")
        subtitle.setObjectName("muted")
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(button('模型下载',self.model_downloads))
        header.addWidget(button("导出结果", self.export, True))
        main.addLayout(header)

        tools = QHBoxLayout()
        self.mode = QComboBox()
        for key, label in MODES.items():
            self.mode.addItem(label, key)
        tools.addWidget(self.mode)
        self.open_button = button("打开文件夹", self.open_folder, True)
        tools.addWidget(self.open_button)
        self.refresh_button = button("刷新扫描", self.refresh)
        tools.addWidget(self.refresh_button)
        self.pause_button = button("暂停", self.pause)
        tools.addWidget(self.pause_button)
        tools.addWidget(button("取消队列", self.cancel))
        tools.addWidget(button("重试失败", self.retry_failed))
        tools.addStretch()
        self.device = QComboBox()
        self.device.addItem("自动选择 GPU / CPU", "auto")
        self.device.addItem("CPU", "cpu")
        if sys.platform!='darwin':self.device.addItem("GPU", "gpu:0")
        tools.addWidget(self.device)
        self.download = QCheckBox("允许下载模型")
        self.download.setToolTip("仅下载公开模型，不上传图片。取消勾选后强制离线运行。")
        self.download.setChecked(False)
        tools.addWidget(self.download)
        main.addLayout(tools)
        self.folder_label = QLabel("选择图片文件夹，自动识别其中已有图片及子文件夹")
        self.folder_label.setObjectName("muted")
        main.addWidget(self.folder_label)

        split = QSplitter()
        main.addWidget(split, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self.filter = QComboBox()
        self.filter.addItems(["全部图片", "待核对", "已核对", "识别失败", "来源已变化"])
        self.filter.currentIndexChanged.connect(self.refresh_list)
        ll.addWidget(self.filter)
        self.files = QListWidget()
        self.files.currentItemChanged.connect(self.select_page)
        ll.addWidget(self.files, 1)
        ll.addWidget(button('重试当前图', self.retry_current, True))
        ll.addWidget(button('重试失败任务', self.retry_failed))
        self.file_summary = QLabel("尚未打开文件夹")
        self.file_summary.setObjectName("muted")
        ll.addWidget(self.file_summary)
        split.addWidget(left)

        middle = QWidget()
        ml = QVBoxLayout(middle)
        ml.setContentsMargins(6, 0, 6, 0)
        image_tools = QHBoxLayout()
        image_tools.addWidget(QLabel("图像核对"))
        image_tools.addStretch()
        self.source_toggle = QCheckBox("查看来源原图")
        self.source_toggle.toggled.connect(self.show_image)
        image_tools.addWidget(self.source_toggle)
        ml.addLayout(image_tools)
        image_buttons = QHBoxLayout()
        image_buttons.addWidget(button("适应", lambda: self.image.fit()))
        image_buttons.addWidget(button("＋", lambda: self.image.scale(1.25, 1.25)))
        image_buttons.addWidget(button("－", lambda: self.image.scale(.8, .8)))
        image_buttons.addWidget(button("旋转视图", lambda: self.image.rotate(90)))
        image_buttons.addWidget(button("框选重识别", self.select_region))
        ml.addLayout(image_buttons)
        self.image = ImageView()
        self.image.selected.connect(self.select_overlay)
        self.image.region.connect(self.recognize_region)
        ml.addWidget(self.image, 1)
        self.image_caption = QLabel("滚轮缩放 · 拖动画布 · 点击文字框定位")
        self.image_caption.setWordWrap(True)
        self.image_caption.setObjectName("muted")
        ml.addWidget(self.image_caption)
        split.addWidget(middle)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        review_tools = QHBoxLayout()
        review_tools.addWidget(QLabel("识别与校对"))
        review_tools.addStretch()
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 1)
        self.threshold.setSingleStep(.05)
        self.threshold.setValue(.85)
        self.threshold.setPrefix("疑点 < ")
        self.threshold.valueChanged.connect(self.render_document)
        review_tools.addWidget(self.threshold)
        rl.addLayout(review_tools)
        row = QHBoxLayout()
        row.addWidget(button("重识别", self.rerun))
        row.addWidget(button("CPU 重试", lambda: self.rerun(cpu=True)))
        self.enhance_button = button("VL 增强", lambda: self.rerun(enhance=True))
        if sys.platform=='darwin':
            self.enhance_button.setEnabled(False)
            self.enhance_button.setToolTip('Mac VL 增强尚未完成适配验证')
        row.addWidget(self.enhance_button)
        row.addWidget(button("版本 / 候选", self.show_candidates))
        rl.addLayout(row)

        self.tabs = QTabWidget()
        self.lines = QTableWidget(0, 2)
        self.lines.setHorizontalHeaderLabels(["识别文字 · 双击修改", "置信度"])
        self.lines.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.lines.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.lines.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lines.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lines.itemChanged.connect(self.edit_line)
        self.lines.currentCellChanged.connect(self.line_selected)
        self.tabs.addTab(self.lines, "逐行校对")
        self.tables_tabs = QTabWidget()
        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.tables_tabs)
        tr = QHBoxLayout()
        for text, action in [("＋行", lambda: self.table_resize("row")), ("－行", lambda: self.table_resize("row", True)),
                             ("＋列", lambda: self.table_resize("col")), ("－列", lambda: self.table_resize("col", True)),
                             ("合并", self.table_merge), ("拆分", self.table_split)]:
            tr.addWidget(button(text, action))
        table_layout.addLayout(tr)
        self.tabs.addTab(table_panel, "表格校对")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.tabs.addTab(self.preview, "全文预览")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.tabs.addTab(self.log_view, "运行日志")
        rl.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        for text, action in [("新增文字", self.add_line), ("删除", self.delete_line), ("上移", lambda: self.move_line(-1)),
                             ("下移", lambda: self.move_line(1)), ("撤销", self.undo)]:
            row.addWidget(button(text, action))
        rl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("下一疑点  F4", self.next_doubt))
        row.addWidget(button("下一张  F6", self.next_page))
        self.review_button = button("标记已核对  Ctrl+Enter", self.mark_reviewed, True)
        row.addWidget(self.review_button)
        rl.addLayout(row)
        self.save_label = QLabel("修改自动保存；重识别结果作为候选保留")
        self.save_label.setObjectName("muted")
        self.save_label.setWordWrap(True)
        rl.addWidget(self.save_label)
        split.addWidget(right)
        split.setSizes([240, 620, 590])
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        split.setCollapsible(2, False)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        main.addWidget(self.progress)
        self.status_label = QLabel("就绪 · 首次使用需要下载模型，日志中可查看进度")
        main.addWidget(self.status_label)
        for key, fn in [("F4", self.next_doubt), ("F6", self.next_page), ("Ctrl+Return", self.mark_reviewed), ("Ctrl+Z", self.undo)]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(fn)
            self.addAction(action)

    def restore_settings(self):
        try:
            settings = json.loads((self.data_dir / "settings.json").read_text(encoding="utf-8"))
            self.code_defaults = options(settings.get('code_options'))
            self.watermark_defaults = watermark_settings(settings.get('color_watermark'))
            self.threshold.setValue(settings.get("threshold", .85))
            self.download.setChecked(settings.get("download", False))
            self.mode.setCurrentIndex(max(0, self.mode.findData(settings.get("mode", "print"))))
            self.root = settings.get("root", "")
            if self.root and Path(self.root).is_dir():
                self.folder_label.setText(self.root)
                self.refresh_list()
                QTimer.singleShot(200, self.enqueue_pending)
        except (OSError, ValueError):
            pass

    def open_folder(self):
        root = QFileDialog.getExistingDirectory(self, "打开图片文件夹", self.root)
        if root:
            self.begin_scan(root)

    def refresh(self):
        if self.root:
            self.begin_scan(self.root)

    def begin_scan(self, root):
        if self.scanner and self.scanner.isRunning():
            return
        self.cancel()
        self.root = str(Path(root).resolve())
        if self.store.watermark_settings(self.root) is None:
            self.store.save_watermark_settings(self.root,self.watermark_defaults)
        self.folder_label.setText(self.root)
        self.status_label.setText("正在扫描图片与文件指纹…")
        self.open_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.scanner = Scanner(self.root, self)
        self.scanner.completed.connect(self.scanned)
        self.scanner.failed.connect(self.scan_failed)
        self.scanner.progress.connect(lambda n: self.status_label.setText(f"扫描中 · 已发现 {n} 页"))
        self.scanner.finished.connect(lambda: self.open_button.setEnabled(True))
        self.scanner.finished.connect(lambda: self.refresh_button.setEnabled(True))
        self.scanner.start()

    def scan_failed(self, error):
        self.status_label.setText("扫描失败：" + error)

    def scanned(self, root, entries):
        self.store.sync(root, entries, self.mode.currentData())
        self.page_id, self.doc = None, None
        self.refresh_list()
        self.enqueue_pending()
        self.save_settings()

    def refresh_list(self, *_):
        self.files.blockSignals(True)
        self.files.clear()
        pages = self.store.pages(self.root) if self.root else []
        selection = None
        for page in pages:
            f = self.filter.currentIndex()
            if (f == 1 and (page["reviewed"] or not page["active_version"])) or (f == 2 and not page["reviewed"]) or (f == 3 and page["status"] != "failed") or (f == 4 and page["status"] != "changed"):
                continue
            label = "已核对 ✓" if page["reviewed"] else STATUS.get(page["status"], page["status"])
            item = QListWidgetItem(f"{Path(page['relative']).name}\n{label}  ·  第 {page['page']+1} 页")
            item.setData(Qt.ItemDataRole.UserRole, page["id"])
            item.setToolTip(page["relative"] + ("\n" + page["error"] if page["error"] else ""))
            if page["status"] == "failed":
                item.setForeground(QColor("#b2513b"))
            self.files.addItem(item)
            if page["id"] == self.page_id:
                selection = item
        self.files.blockSignals(False)
        if selection:
            self.files.blockSignals(True)
            self.files.setCurrentItem(selection)
            self.files.blockSignals(False)
        elif self.files.count():
            self.files.setCurrentRow(0)
        else:
            self.page_id, self.doc = None, None
            self.render_document()
            self.image.scene().clear()
        self.file_summary.setText(f"{len(pages)} 页 · {sum(p['reviewed'] for p in pages)} 页已核对")
        self.update_progress()

    def select_page(self, current, previous=None):
        if not current:
            return
        self.page_id = current.data(Qt.ItemDataRole.UserRole)
        self.doc = self.store.document(self.page_id)
        self.source_toggle.blockSignals(True)
        self.source_toggle.setChecked(False)
        self.source_toggle.blockSignals(False)
        self.render_document()
        self.show_image()
        page = self.store.page(self.page_id)
        self.status_label.setText(page["relative"] + (" · " + page["error"] if page["error"] else ""))

    def show_image(self, *_):
        if not self.page_id:
            return
        page = self.store.page(self.page_id)
        try:
            if self.doc and self.doc.get("view_path") and Path(self.doc["view_path"]).exists() and not self.source_toggle.isChecked():
                path = self.doc["view_path"]
            else:
                image = read_page(Path(page["root"]) / page["relative"], page["page"])
                path = self.data_dir / "previews" / f"source-{page['id']}-{page['fingerprint'][:12]}.png"
                path.parent.mkdir(exist_ok=True)
                if not path.exists():
                    image.save(path)
            overlays = []
            if self.doc and not self.source_toggle.isChecked():
                for b in self.doc["blocks"]:
                    overlays.append((b["id"], b["polygon"], self.doubt(b)))
                for table in self.doc["tables"]:
                    for cell in table["cells"]:
                        overlays.append((f"{table['id']}:{cell['row']}:{cell['col']}", cell.get("polygon") or table.get("polygon", []), False))
            self.view_path = str(path)
            self.image.load(path, overlays)
            corrected = self.doc and self.doc.get("coordinate_space") == "corrected"
            label = "来源原图（无标注）" if self.source_toggle.isChecked() else "校正视图 · 标注坐标属于此图" if corrected else "原图视图 · 点击标注可定位文字"
            if self.doc and self.doc.get('color_watermark') and not self.source_toggle.isChecked():
                label += f" · 识别前按颜色去水印（匹配 {self.doc['color_watermark'].get('fraction',0):.1%}）"
            self.image_caption.setText(label)
        except Exception as exc:
            self.image.scene().clear()
            self.image.scene().addText("图片无法加载")
            self.image_caption.setText(str(exc))

    def doubt(self, item):
        if item.get('kind')=='code' and not item.get('text') and not item.get('issues'):
            return False
        return bool(item.get('issues')) or item.get("confidence") is None or item["confidence"] < self.threshold.value()

    def render_document(self, *_):
        self.loading = True
        self.lines.setRowCount(0)
        for i, b in enumerate(self.doc["blocks"] if self.doc else []):
            self.lines.insertRow(i)
            item = QTableWidgetItem(b["text"])
            if b.get("table_id"):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip("请切换到表格校对编辑单元格")
            self.lines.setItem(i, 0, item)
            confidence = QTableWidgetItem("未知" if b["confidence"] is None else f"{b['confidence']:.1%}")
            confidence.setFlags(confidence.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.lines.setItem(i, 1, confidence)
            if self.doubt(b):
                item.setBackground(QColor("#fff4dc"))
                confidence.setForeground(QColor("#a56b12"))
        self.lines.resizeRowsToContents()
        while self.tables_tabs.count():
            widget = self.tables_tabs.widget(0)
            self.tables_tabs.removeTab(0)
            widget.deleteLater()
        for i, table in enumerate(self.doc["tables"] if self.doc else [], 1):
            grid = TableGrid(table, self.threshold.value())
            grid.edited.connect(self.save)
            grid.cell_selected.connect(self.image.highlight)
            self.tables_tabs.addTab(grid, f"表格 {i}")
        self.preview.setPlainText(render_text(self.doc) if self.doc else "识别完成后可在此查看全文。")
        if hasattr(self, 'code_editor'):
            self.render_code()
        self.loading = False
        self.update_review_label()

    def update_review_label(self):
        reviewed = self.page_id and self.store.page(self.page_id)["reviewed"]
        self.review_button.setText("已核对 ✓ · 点击撤回" if reviewed else "标记已核对  Ctrl+Enter")

    def edit_line(self, item):
        if self.loading or not self.doc or item.column() != 0:
            return
        self.doc["blocks"][item.row()]["text"] = item.text()
        self.save()
        if self.doc.get('mode') == 'code':
            self.render_code()

    def save(self):
        if self.loading or not self.doc or not self.page_id:
            return
        try:
            self.store.save_edit(self.page_id, self.doc)
            self.preview.setPlainText(render_text(self.doc))
            self.save_label.setText("已自动保存 " + time.strftime("%H:%M:%S") + " · Ctrl+Z 撤销")
            self.update_review_label()
            self.refresh_list()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def line_selected(self, row, *_):
        if self.doc and 0 <= row < len(self.doc["blocks"]):
            self.image.highlight(self.doc["blocks"][row]["id"])

    def select_overlay(self, key):
        if not self.doc:
            return
        if ":" in key:
            table_id, row, col = key.split(":")
            for index, t in enumerate(self.doc["tables"]):
                if t["id"] == table_id:
                    self.tabs.setCurrentIndex(1)
                    self.tables_tabs.setCurrentIndex(index)
                    self.tables_tabs.widget(index).setCurrentCell(int(row), int(col))
        else:
            for index, b in enumerate(self.doc["blocks"]):
                if b["id"] == key:
                    self.tabs.setCurrentIndex(0)
                    self.lines.selectRow(index)
                    self.lines.scrollToItem(self.lines.item(index, 0))
                    if self.doc.get('mode') == 'code':
                        self.tabs.setCurrentWidget(self.code_editor)
                        self.code_editor.goto(index)
                    break
        self.image.highlight(key)

    def add_line(self):
        if not self.page_id:
            return
        if not self.doc:
            self.doc = new_document(int(self.image.image_rect.width()), int(self.image.image_rect.height()), view_path=self.view_path)
            self.store.add_result(self.page_id, self.doc)
        self.doc["blocks"].append(block("请输入漏识别文字"))
        self.save()
        self.render_document()
        self.lines.setCurrentCell(len(self.doc["blocks"])-1, 0)
        self.lines.editItem(self.lines.currentItem())

    def delete_line(self):
        row = self.lines.currentRow()
        if self.doc and 0 <= row < len(self.doc["blocks"]):
            b = self.doc["blocks"].pop(row)
            self.doc["tables"] = [t for t in self.doc["tables"] if t["id"] != b.get("table_id")]
            self.save()
            self.render_document()
            self.show_image()

    def move_line(self, delta):
        row = self.lines.currentRow()
        if self.doc and 0 <= row < len(self.doc["blocks"]) and 0 <= row+delta < len(self.doc["blocks"]):
            blocks = self.doc["blocks"]
            blocks[row], blocks[row+delta] = blocks[row+delta], blocks[row]
            self.save()
            self.render_document()
            self.lines.selectRow(row+delta)

    def undo(self):
        if self.page_id and self.doc:
            self.doc = self.store.undo(self.page_id)
            self.render_document()
            self.show_image()
            self.refresh_list()

    def mark_reviewed(self):
        if self.doc:
            page = self.store.page(self.page_id)
            if page["status"] == "changed":
                QMessageBox.information(self, "来源已变化", "请先重新识别并核对当前图片。旧版本仍保留。")
                return
            try:
                self.store.save_edit(self.page_id, self.doc, not page["reviewed"])
            except ValueError as exc:
                QMessageBox.information(self, "需要核对当前图片", str(exc))
                return
            self.refresh_list()
            self.update_review_label()

    def next_doubt(self):
        if not self.doc or not self.doc["blocks"]:
            return
        if self.tabs.currentIndex() == 1:
            grid = self.tables_tabs.currentWidget()
            if grid:
                cells = sorted(grid.table["cells"], key=lambda c: (c["row"], c["col"]))
                start = next((i for i, c in enumerate(cells) if (c["row"], c["col"]) == (grid.currentRow(), grid.currentColumn())), -1)
                for offset in range(1, len(cells)+1):
                    cell = cells[(start+offset) % len(cells)]
                    if self.doubt(cell):
                        grid.setCurrentCell(cell["row"], cell["col"])
                        return
        start = self.lines.currentRow()
        for offset in range(1, len(self.doc["blocks"])+1):
            row = (start+offset) % len(self.doc["blocks"])
            if self.doubt(self.doc["blocks"][row]):
                table_id = self.doc["blocks"][row].get("table_id")
                if table_id:
                    for index, table in enumerate(self.doc["tables"]):
                        if table["id"] == table_id:
                            self.tabs.setCurrentIndex(1)
                            self.tables_tabs.setCurrentIndex(index)
                            self.tables_tabs.widget(index).setCurrentCell(0, 0)
                            return
                self.tabs.setCurrentIndex(0)
                self.lines.selectRow(row)
                self.lines.scrollToItem(self.lines.item(row, 0))
                if self.doc.get('mode') == 'code':
                    self.tabs.setCurrentWidget(self.code_editor)
                    self.code_editor.goto(row)
                return

    def next_page(self):
        if self.files.count():
            self.files.setCurrentRow((self.files.currentRow()+1) % self.files.count())

    def table_resize(self, axis, delete=False):
        grid = self.tables_tabs.currentWidget()
        if grid:
            index = max(0, grid.currentRow() if axis == "row" else grid.currentColumn())
            resize_table(grid.table, axis, index if delete else index+1, delete)
            grid.refresh()
            self.save()
            self.show_image()

    def table_merge(self):
        grid = self.tables_tabs.currentWidget()
        if grid and grid.selectedRanges():
            selected = grid.selectedRanges()[0]
            try:
                merge_cells(grid.table, selected.topRow(), selected.leftColumn(), selected.bottomRow(), selected.rightColumn())
                grid.refresh()
                self.save()
                self.show_image()
            except ValueError as exc:
                QMessageBox.information(self, "合并单元格", str(exc))

    def table_split(self):
        grid = self.tables_tabs.currentWidget()
        if grid:
            for cell in grid.table["cells"]:
                if cell["row"] <= grid.currentRow() < cell["row"]+cell["rowspan"] and cell["col"] <= grid.currentColumn() < cell["col"]+cell["colspan"]:
                    cell["rowspan"], cell["colspan"] = 1, 1
                    break
            fill_table(grid.table)
            grid.refresh()
            self.save()
            self.show_image()

    def job(self, page, **overrides):
        return {"job_id": uid(), "page_id": page["id"], "path": str(Path(page["root"]) / page["relative"]),
                "page": page["page"], "fingerprint": page["fingerprint"], "mode": page["mode"],
                "device": self.device.currentData(), "offline": not self.download.isChecked(),
                "code_options": self.current_code_options(page['id']),
                "color_watermark": self.current_watermark_settings(),
                "data_dir": str(self.data_dir), **overrides}

    def enqueue_pending(self):
        jobs = []
        for page in self.store.pages(self.root):
            if page['status'] == 'pending':
                jobs.append(self.retry_job(page))
            elif page['status'] in ('done','empty'):
                cfg = self.current_code_options(page['id'])
                request = self.store.last_request(page['id'])
                code_changed=page['mode']=='code' and request and config_key(request.get('code_options'))!=config_key(cfg)
                color_changed=request and watermark_key(request.get('color_watermark'))!=watermark_key(self.current_watermark_settings())
                if request and not request.get('roi') and (code_changed or color_changed):
                    jobs.append(self.job(page, candidate=True))
        self.queue.add(jobs)

    def rerun(self, checked=False, cpu=False, enhance=False):
        if self.page_id:
            if cpu:
                self.retry_current(cpu=True)
                return
            self.store.set_mode(self.page_id, self.mode.currentData())
            page = self.store.page(self.page_id)
            self.queue.retry(self.job(page, device=self.device.currentData(), enhance=enhance, candidate=bool(self.doc)))

    def retry_current(self, checked=False, cpu=False):
        if not self.page_id:
            self.status_label.setText('请先在左侧选择需要重试的图片。')
            return
        job = self.retry_job(self.store.page(self.page_id), cpu=cpu)
        self.store.save_request(self.page_id, job)
        self.queue.retry(job)
        self.pause_button.setText('暂停')
        self.status_label.setText('已重试当前图，保留上次识别与去水印配置；修改颜色后请在去水印窗口重新应用。')

    def select_region(self):
        if self.page_id and self.doc and self.view_path and not self.source_toggle.isChecked():
            self.image.select_region = True
            self.code_action = None
            self.image_caption.setText("请拖动框选区域；局部结果作为候选，可替换当前行或新增文字")

    def recognize_region(self, rect):
        if self.code_region_selected(rect):
            return
        page = self.store.page(self.page_id)
        cfg = self.current_code_options()
        from .codeocr import intersects
        cfg['watermark_regions'] = [[max(0,r[0]-rect[0]),max(0,r[1]-rect[1]),min(rect[2],r[2])-rect[0],min(rect[3],r[3])-rect[1]]
                                    for r in cfg['watermark_regions'] if intersects(rect,r)]
        cfg.update(regions=[],corners=[],image_type='screenshot')
        self.queue.add([self.job(page, mode="code" if self.doc and self.doc.get('mode')=='code' else "print", view_source=self.view_path, roi=rect, code_options=cfg,
                                 skip_color_watermark=True,
                                 target_id=self.doc["blocks"][self.lines.currentRow()]["id"] if self.doc and self.lines.currentRow() >= 0 else None,
                                 candidate=True, parent_view=self.view_path)])

    def retry_failed(self):
        jobs = [self.retry_job(p) for p in self.store.pages(self.root) if p["status"] in {"failed", "cancelled"}]
        self.queue.paused = False
        self.pause_button.setText('暂停')
        self.queue.add(jobs)
        self.status_label.setText(f'已安排重试 {len(jobs)} 个失败或取消任务。' if jobs else '没有失败或取消的任务；可选择图片后点击「重试当前图」。')

    def retry_job(self, page, cpu=False):
        job = self.store.last_request(page["id"]) or self.job(page)
        job.update(job_id=uid(), offline=not self.download.isChecked(), data_dir=str(self.data_dir))
        if cpu:
            job["device"] = "cpu"
        return job

    def pause(self):
        self.queue.toggle_pause()
        self.pause_button.setText("继续" if self.queue.paused else "暂停")
        if self.queue.paused:
            self.status_label.setText("已暂停队列；当前图片处理完毕后停止")

    def cancel(self):
        for job in self.queue.cancel():
            self.store.status(job["page_id"], "cancelled")
        self.pause_button.setText("暂停")
        self.refresh_list()

    def job_started(self, job):
        self.store.save_request(job["page_id"], job)
        if job['mode']=='code' and not job.get('roi'):
            self.store.save_code_options(job['page_id'],job.get('code_options',{}))
        self.store.status(job["page_id"], "running")
        self.status_label.setText("正在识别 " + Path(job["path"]).name + " · 首次加载模型可能需要较长时间")
        if job['mode']=='code' and job.get('enhance') and job.get('device')=='cpu':
            self.status_label.setText('正在进行代码增强 CPU 推理 · 本机样本约 1–3 分钟，可取消；结果作为候选保存')
        self.refresh_list()

    def job_completed(self, job, doc):
        doc["target_id"] = job.get("target_id")
        doc["parent_view"] = job.get("parent_view")
        version, candidate = self.store.add_result(job["page_id"], doc, job.get("candidate", False),
                                                 replace_pristine=bool(doc.get('color_watermark')) and not job.get('roi'))
        if self.page_id == job["page_id"]:
            self.doc = self.store.document(self.page_id)
            self.render_document()
            self.show_image()
        self.refresh_list()
        self.status_label.setText(f"{Path(job['path']).name} · {doc.get('engine')} · {doc.get('device')} · {doc.get('inference_seconds', 0):.1f} 秒"
                                 + (" · 新候选已保存，请在「版本 / 候选」中查看" if candidate else ""))
        if doc.get('color_watermark'):
            self.status_label.setText(self.status_label.text() + ' · 点击「查看去水印图」查看实际处理结果')

    def job_failed(self, job, error):
        self.store.status(job["page_id"], "failed", error)
        self.refresh_list()
        self.status_label.setText("识别失败：" + error[:200])

    def update_progress(self):
        if not hasattr(self, "progress"):
            return
        pages = self.store.pages(self.root) if self.root else []
        success = sum(p["status"] in {"done", "empty"} for p in pages)
        failed = sum(p["status"] == "failed" for p in pages)
        self.progress.setRange(0, max(1, len(pages)))
        self.progress.setValue(success+failed)
        self.progress.setFormat(f"{success+failed} / {len(pages)} 页 · 成功 {success} · 失败 {failed} · 队列 {len(self.queue.waiting)}")

    def append_log(self, text):
        self.log_view.appendPlainText(text.strip())

    def show_candidates(self):
        if not self.page_id:
            return
        versions = self.store.versions(self.page_id)
        if not versions:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("识别版本与候选 · 采纳可撤销")
        dialog.resize(1000, 720)
        layout = QVBoxLayout(dialog)
        choices = QComboBox()
        for v in versions:
            choices.addItem(f"{'候选' if v['candidate'] else '识别原稿'} #{v['id']} · {v['mode']} · " + time.strftime("%m-%d %H:%M:%S", time.localtime(v["created"])), v["id"])
        layout.addWidget(choices)
        split = QSplitter()
        current = QPlainTextEdit(render_text(self.doc) if self.doc else "暂无当前稿")
        current.setReadOnly(True)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        if self.doc and self.doc.get('mode')=='code':
            for editor in (current,preview):
                editor.setStyleSheet('QPlainTextEdit { font-family: Consolas; font-size: 14px; }')
                editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        split.addWidget(current)
        split.addWidget(preview)
        layout.addWidget(QLabel("左侧：当前修订稿　　右侧：所选识别原稿 / 候选"))
        layout.addWidget(split, 1)
        note = QLabel()
        note.setWordWrap(True)
        layout.addWidget(note)
        def change():
            candidate = self.store.version_document(choices.currentData(), original=True)
            preview.setPlainText(render_text(candidate))
            note.setText("局部候选：采纳时替换框选时选中的文字行；没有选中行则新增。" if candidate.get("roi") else "整页候选：采纳后成为当前修订稿，原稿及历史版本保留，可撤销。")
        choices.currentIndexChanged.connect(change)
        change()
        row = QHBoxLayout()
        def accept_selection():
            if self.doc and self.doc.get('mode')=='code':
                text = preview.textCursor().selectedText().replace('\u2029','\n')
                if text:
                    cursor = self.code_editor.textCursor()
                    cursor.insertText(text)
                    self.code_editor.setTextCursor(cursor)
                    dialog.accept()
        def accept():
            candidate = self.store.version_document(choices.currentData(), original=True)
            if candidate.get("roi"):
                if not self.doc or candidate.get("parent_view") != self.doc.get("view_path"):
                    QMessageBox.information(dialog, "视图已变化", "局部候选属于其他图像版本，请重新框选识别。")
                    return
                text = render_text(candidate)
                target = next((b for b in self.doc["blocks"] if b["id"] == candidate.get("target_id") and not b.get("table_id")), None)
                if self.doc.get('mode') == 'code':
                    from .codeocr import accept_region
                    accept_region(self.doc,candidate)
                elif target:
                    target["text"] = text
                else:
                    from .engines import polygon
                    self.doc["blocks"].append(block(text, polygon(candidate["roi"])))
                self.save()
            else:
                if not self.doc:
                    with self.store.db:
                        self.store.db.execute("UPDATE pages SET active_version=? WHERE id=?", (choices.currentData(), self.page_id))
                else:
                    self.store.accept(self.page_id, choices.currentData())
                self.doc = self.store.document(self.page_id)
            self.render_document()
            self.show_image()
            self.refresh_list()
            dialog.accept()
        row.addStretch()
        if self.doc and self.doc.get('mode')=='code':
            row.addWidget(button('采纳选中文字到编辑光标处',accept_selection))
        row.addWidget(button("采纳所选结果", accept, True))
        row.addWidget(button("关闭", dialog.reject))
        layout.addLayout(row)
        dialog.exec()

    def export(self):
        if not self.root:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("导出识别结果")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("导出逐图 TXT / Markdown / JSON、合并全文与表格 XLSX。"))
        only_reviewed = QCheckBox("仅导出已核对内容")
        layout.addWidget(only_reviewed)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置", self.root)
        if folder:
            output = Path(folder) / "ocr-export" / time.strftime("%Y%m%d-%H%M%S")
            try:
                count = export_project(self.store, self.root, output, only_reviewed.isChecked())
                self.status_label.setText(f"已导出 {count} 页到 {output}")
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))
            except Exception as exc:
                QMessageBox.critical(self, "导出失败", str(exc))

    def model_downloads(self):
        from .model_ui import ModelDialog
        ModelDialog(self).exec()

    def save_settings(self):
        (self.data_dir / "settings.json").write_text(json.dumps({"root": self.root, "mode": self.mode.currentData(),
            "code_options": self.code_defaults,
            "color_watermark": self.watermark_defaults,
            "threshold": self.threshold.value(), "download": self.download.isChecked()}, ensure_ascii=False), encoding="utf-8")

    def closeEvent(self, event):
        if self.scanner and self.scanner.isRunning():
            self.scanner.requestInterruption()
            self.scanner.wait()
        for job in self.queue.cancel():
            self.store.status(job["page_id"], "pending")
        self.save_settings()
        self.store.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LocalOCRReview")
    configure_app(app)
    lock = QLockFile(str(app_data() / "application.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "识文已运行", "当前数据目录已由另一个识文窗口打开。")
        return
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

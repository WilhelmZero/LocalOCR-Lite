from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextOption, QTextFormat
from PySide6.QtWidgets import (QPlainTextEdit, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QComboBox, QCheckBox, QLineEdit, QLabel, QPushButton, QMessageBox, QListWidget, QTextEdit, QSplitter)

from .codeocr import options, LANGUAGES, edit_code, config_key
from .exporting import render_text


class Highlight(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.language = 'text'

    def highlightBlock(self, text):
        if self.language == 'text':
            return
        # Highlighting is presentation only; it never rewrites code.
        patterns = [(r'\b(?:def|class|return|if|else|elif|for|while|import|from|try|except|with|as|in|True|False|None|function|const|let|var|new|public|private|static|void|int|double|float|boolean|throw|throws|async|await|select|SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|null|true|false)\b', '#6544ac'),
                    (r'\b\d+(?:\.\d+)?\b', '#ad5a10'), (r'"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27', '#187647')]
        for pattern, color in patterns:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            for match in re.finditer(pattern, text):
                start = len(text[:match.start()].encode('utf-16-le'))//2
                length = len(match.group().encode('utf-16-le'))//2
                self.setFormat(start, length, fmt)


class Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor('#edf3ef'))
        editor = self.editor
        block = editor.firstVisibleBlock()
        while block.isValid():
            top = int(editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top())
            if top > event.rect().bottom():
                break
            if block.isVisible():
                painter.setPen(QColor('#9d6815' if block.blockNumber() in editor.issues else '#74877d'))
                painter.drawText(0, top, self.width()-6, int(editor.fontMetrics().height()), Qt.AlignmentFlag.AlignRight, str(block.blockNumber()+1))
            block = block.next()


class CodeEditor(QPlainTextEdit):
    line_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setStyleSheet("QPlainTextEdit { font-family: Consolas; font-size: 14px; }")
        self.setFont(QFont('Consolas', 11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setUndoRedoEnabled(False)  # The application revision history owns Ctrl+Z.
        self.gutter = Gutter(self)
        self.issues = set()
        self.indent = 4
        self.highlighter = Highlight(self.document())
        self.blockCountChanged.connect(self.layout_gutter)
        self.updateRequest.connect(lambda *_: self.gutter.update())
        self.cursorPositionChanged.connect(lambda: self.line_selected.emit(self.textCursor().blockNumber()))
        self.layout_gutter()

    def layout_gutter(self, *_):
        width = 16 + self.fontMetrics().horizontalAdvance('9') * len(str(max(1,self.blockCount())))
        self.setViewportMargins(width,0,0,0)
        self.gutter.setGeometry(QRect(0,0,width,self.height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout_gutter()

    def keyPressEvent(self, event):
        if event.key()==Qt.Key.Key_Tab and not event.modifiers():
            self.textCursor().insertText(' '*self.indent)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setPen(QColor('#e0e8e4'))
        block = self.firstVisibleBlock()
        step = self.fontMetrics().horizontalAdvance(' ')
        while block.isValid():
            rect = self.blockBoundingGeometry(block).translated(self.contentOffset())
            if rect.top() > self.viewport().height():
                break
            n = len(block.text())-len(block.text().lstrip(' '))
            for col in range(self.indent,n+1,self.indent):
                x = int(self.contentOffset().x()+col*step)
                painter.drawLine(x,int(rect.top()),x,int(rect.bottom()))
            block = block.next()

    def whitespace(self, enabled):
        option = self.document().defaultTextOption()
        option.setFlags(QTextOption.Flag.ShowTabsAndSpaces if enabled else QTextOption.Flag(0))
        self.document().setDefaultTextOption(option)

    def goto(self, line):
        cursor = QTextCursor(self.document().findBlockByNumber(line))
        if cursor.isNull():
            return
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def show_issues(self, issues):
        self.issues = issues
        selections=[]
        for line in sorted(issues):
            cursor=QTextCursor(self.document().findBlockByNumber(line))
            if cursor.isNull():
                continue
            selection=QTextEdit.ExtraSelection()
            selection.cursor=cursor
            selection.format.setBackground(QColor('#fff6df'))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection,True)
            selections.append(selection)
        self.setExtraSelections(selections)
        self.gutter.update()


class CodeMixin:
    def build_code_ui(self):
        self.code_defaults = options()
        self.code_action = None
        self.code_editor = CodeEditor()
        self.code_editor.textChanged.connect(self.code_edited)
        self.code_editor.line_selected.connect(self.code_selected)
        self.tabs.addTab(self.code_editor, '代码校对')
        self.code_toolbar = QWidget()
        row = QHBoxLayout(self.code_toolbar)
        row.setContentsMargins(0,0,0,0)
        for label, callback in [('代码设置',self.code_settings), ('添加代码区域',lambda: self.code_select('region')),
                                ('标记水印并设置去除',lambda: self.code_select('watermark')), ('选择屏幕四角',self.code_corners),
                                ('水印候选 / 恢复',self.watermark_candidates)]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            row.addWidget(b)
        spaces = QCheckBox('显示空白')
        spaces.toggled.connect(self.code_editor.whitespace)
        row.addWidget(spaces)
        self.centralWidget().layout().insertWidget(2,self.code_toolbar)
        self.mode.currentIndexChanged.connect(lambda: self.code_toolbar.setVisible(self.mode.currentData()=='code'))
        self.mode.currentIndexChanged.connect(lambda: self.enhance_button.setText('代码增强' if self.mode.currentData()=='code' else 'VL 增强'))
        self.code_toolbar.setVisible(self.mode.currentData()=='code')
        self.image.corners_selected.connect(self.code_corners_selected)

    def current_code_options(self, page_id=None):
        page_id = page_id or self.page_id
        saved = self.store.code_options(page_id) if page_id else None
        cfg=options(saved or self.code_defaults)
        if saved and saved.get('source_fingerprint') and saved['source_fingerprint']!=self.store.page(page_id)['fingerprint']:
            cfg.update(regions=[],watermark_regions=[],corners=[])
        return cfg

    def code_settings(self):
        cfg = self.current_code_options()
        dialog = QDialog(self)
        dialog.setWindowTitle('代码识别设置 · 保存后重新识别生效')
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        language = QComboBox()
        language.addItems(list(LANGUAGES))
        language.setCurrentText(cfg['language'])
        photo = QComboBox()
        photo.addItems(['截图','屏幕拍照'])
        photo.setCurrentIndex(cfg['image_type']=='photo')
        indent = QComboBox()
        indent.addItems(['4','2'])
        indent.setCurrentText(str(cfg['indent']))
        watermark = QLineEdit(cfg['watermark_text'])
        filtering = QCheckBox('开启保守水印过滤')
        filtering.setChecked(cfg['filter_watermark'])
        gutter = QCheckBox('排除独立连续行号栏')
        gutter.setChecked(cfg['remove_line_numbers'])
        for name,widget in [('语言',language),('图片类型',photo),('缩进宽度',indent),('水印文字',watermark),('',filtering),('',gutter)]:
            form.addRow(name,widget)
        layout.addLayout(form)
        layout.addWidget(QLabel('区域顺序决定代码拼接顺序；区域坐标属于校正视图。'))
        regions = QListWidget()
        entries = [('regions',v) for v in cfg['regions']] + [('watermark_regions',v) for v in cfg['watermark_regions']]
        def refresh():
            regions.clear()
            for key,value in entries:
                regions.addItem(('代码区域' if key=='regions' else '水印区域') + ' '+str([round(x) for x in value]))
        refresh()
        layout.addWidget(regions)
        row = QHBoxLayout()
        def move(delta):
            i = regions.currentRow()
            if 0<=i<len(entries) and 0<=i+delta<len(entries):
                entries[i],entries[i+delta] = entries[i+delta],entries[i]
                refresh()
                regions.setCurrentRow(i+delta)
        def remove():
            i = regions.currentRow()
            if i>=0:
                entries.pop(i)
                refresh()
        for label,fn in [('上移',lambda:move(-1)),('下移',lambda:move(1)),('删除区域',remove)]:
            b = QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        layout.addLayout(row)
        reset = QCheckBox('清除四角校正（同时清除所有区域）')
        layout.addWidget(reset)
        def apply():
            old_image_type=cfg['image_type']
            cfg.update(language=language.currentText(), image_type='photo' if photo.currentIndex() else 'screenshot', indent=int(indent.currentText()),
                       watermark_text=watermark.text(), filter_watermark=filtering.isChecked(), remove_line_numbers=gutter.isChecked())
            for key in ('regions','watermark_regions'):
                cfg[key] = [v for k,v in entries if k==key]
            if reset.isChecked():
                cfg.update(corners=[],regions=[],watermark_regions=[])
            elif old_image_type!=cfg['image_type']:
                cfg.update(regions=[],watermark_regions=[])
            self.code_defaults = options({k:v for k,v in cfg.items() if k not in ('regions','watermark_regions','corners','source_fingerprint')})
            if self.page_id:
                self.store.save_code_options(self.page_id,cfg)
                if self.doc and self.doc.get('mode')=='code':
                    self.doc.setdefault('code_options',{})['language']=cfg['language']
                    self.doc['code_options']['indent']=cfg['indent']
                    self.save()
                    self.render_code()
            self.save_settings()
            dialog.accept()
        b = QPushButton('保存设置'); b.clicked.connect(apply); layout.addWidget(b)
        dialog.exec()

    def code_select(self, action):
        if not self.page_id or not self.doc or not self.view_path or self.source_toggle.isChecked():
            self.status_label.setText('请先完成一次识别并显示校正视图，再框选区域。')
            return
        current=self.current_code_options()
        active=options(self.doc.get('code_options'))
        if current['corners']!=active['corners'] or current['image_type']!=active['image_type']:
            self.status_label.setText('校正设置已变化，请先重新识别并采纳新的校正视图，再框选区域。')
            return
        self.code_action = action
        self.image.select_region = True
        self.image_caption.setText('框选代码区域后点击重识别' if action=='region' else '框选水印干扰区域后，将打开取色去水印窗口；矩形用于标注干扰，不整块擦除代码。')

    def code_region_selected(self, rect):
        if not self.code_action:
            return False
        cfg = self.current_code_options()
        cfg['regions' if self.code_action=='region' else 'watermark_regions'].append(rect)
        self.store.save_code_options(self.page_id,cfg)
        watermark = self.code_action == 'watermark'
        self.code_action = None
        self.status_label.setText('区域已保存，点击重识别后生效；代码设置中可删除或调整顺序。')
        if watermark:
            self.watermark_dialog()
        return True

    def code_corners(self):
        if not self.page_id:
            return
        self.source_toggle.setChecked(True)
        self.image.corner_points = []
        self.image.pick_corners = True
        self.image.select_region = False
        self.image_caption.setText('依次点击屏幕左上、右上、右下、左下四角；Esc 取消')

    def code_corners_selected(self, points):
        cfg = self.current_code_options()
        cfg.update(corners=points, regions=[], watermark_regions=[], image_type='photo')
        self.store.save_code_options(self.page_id,cfg)
        self.status_label.setText('四角已保存，重识别后在新校正视图中重新框选代码 / 水印区域。')

    def render_code(self):
        is_code = self.doc and self.doc.get('mode')=='code'
        self.code_editor.setEnabled(bool(is_code))
        self.code_editor.blockSignals(True)
        self.code_editor.setPlainText(render_text(self.doc) if is_code else '')
        cfg = options(self.doc.get('code_options')) if is_code else self.code_defaults
        self.code_editor.highlighter.language = cfg['language']
        self.code_editor.highlighter.rehighlight()
        self.code_editor.indent = cfg['indent']
        self.code_editor.setTabStopDistance(self.code_editor.fontMetrics().horizontalAdvance(' ')*cfg['indent'])
        self.code_editor.show_issues({i for i,b in enumerate(self.doc['blocks']) if self.doubt(b)} if is_code else set())
        self.code_editor.blockSignals(False)
        if is_code:
            self.tabs.setCurrentWidget(self.code_editor)

    def code_edited(self):
        if self.loading or not self.doc or self.doc.get('mode')!='code':
            return
        edit_code(self.doc,self.code_editor.toPlainText())
        self.save()
        # Refresh the alternate line table without resetting the editing cursor.
        self.loading = True
        self.lines.setRowCount(len(self.doc['blocks']))
        from PySide6.QtWidgets import QTableWidgetItem
        for i,b in enumerate(self.doc['blocks']):
            self.lines.setItem(i,0,QTableWidgetItem(b['text']))
            self.lines.setItem(i,1,QTableWidgetItem('未知' if b['confidence'] is None else f"{b['confidence']:.1%}"))
        self.loading = False
        self.code_editor.show_issues({i for i,b in enumerate(self.doc['blocks']) if self.doubt(b)})

    def code_selected(self, row):
        if self.doc and self.doc.get('mode')=='code' and row<len(self.doc['blocks']):
            self.lines.selectRow(row)
            self.image.highlight(self.doc['blocks'][row]['id'])
            self.save_label.setText('；'.join(self.doc['blocks'][row].get('issues',[])) or '当前行无额外疑点 · 修改自动保存')

    def watermark_candidates(self):
        if not self.doc or self.doc.get('mode')!='code':
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('水印处理候选 · 不自动替换代码')
        dialog.resize(1200,760)
        layout = QVBoxLayout(dialog)
        from .widgets import ImageView
        images=ImageView()
        image_path=self.doc.get('watermark_view') or self.doc.get('view_path')
        if image_path:
            images.load(image_path,[])
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setStyleSheet('QPlainTextEdit { font-family: Consolas; font-size: 14px; }')
        preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        candidate = self.doc.get('watermark_candidate')
        preview.setPlainText(render_text({**candidate,'mode':'code'}) if candidate else '没有处理图候选；可标记水印区域后重新识别。')
        split=QSplitter()
        split.addWidget(images)
        split.addWidget(preview)
        split.setSizes([600,600])
        layout.addWidget(split,1)
        original=QCheckBox('左侧显示原识别图（取消勾选显示处理图）；滚轮可缩放')
        original.toggled.connect(lambda checked: images.load(self.doc.get('view_path') if checked else image_path,[]))
        layout.addWidget(original)
        records = QListWidget()
        for r in self.doc.get('filter_records',[]):
            records.addItem(r['text']+' · '+r['reason'])
        layout.addWidget(QLabel('已排除内容（选中可恢复）'))
        layout.addWidget(records)
        row = QHBoxLayout()
        def restore():
            i = records.currentRow()
            if i>=0:
                value = self.doc['filter_records'].pop(i)
                self.doc['blocks'].append(value)
                self.save(); self.render_document(); self.show_image(); dialog.accept()
        def adopt():
            selection = preview.textCursor().selectedText().replace('\u2029','\n')
            cursor = self.code_editor.textCursor()
            if selection:
                cursor.insertText(selection)
                self.code_editor.setTextCursor(cursor)
                dialog.accept()
        for label,fn in [('采纳选中文字到编辑光标处',adopt),('恢复选中排除项',restore)]:
            b=QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        layout.addLayout(row)
        dialog.exec()
        self.show_image()

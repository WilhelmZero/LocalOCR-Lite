from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QPixmap, QImage
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QCheckBox,
    QComboBox, QDoubleSpinBox, QSpinBox, QPushButton, QLabel, QListWidget, QColorDialog,
    QSplitter, QMessageBox, QFileDialog)

from .widgets import ImageView
from .watermark import settings, profile_key, remove_color, export_cleaned
from .core import read_page,uid


def pixmap(image):
    rgb=image.convert('RGB')
    data=rgb.tobytes()
    return QPixmap.fromImage(QImage(data,rgb.width,rgb.height,rgb.width*3,QImage.Format.Format_RGB888).copy())


class Preview(QThread):
    ready=Signal(int,object,object,dict)
    failed=Signal(int,str)

    def __init__(self,image,cfg,generation,parent):
        super().__init__(parent)
        self.image,self.cfg,self.generation=image,cfg,generation

    def run(self):
        try:
            cleaned,mask,stats=remove_color(self.image,self.cfg)
            self.ready.emit(self.generation,cleaned,mask,stats)
        except Exception as exc:
            self.failed.emit(self.generation,str(exc))


class WatermarkDialog(QDialog):
    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.cfg=window.current_watermark_settings()
        self.image=None
        self.cleaned,self.mask=None,None
        self.worker=None
        self.generation=0
        self.valid_preview=None
        self.closing=None
        self.action=None
        self.setWindowTitle('按颜色批量去水印 · 处理图再识别')
        self.resize(1280,850)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('1 选择样图并吸取水印颜色　→　2 检查红色匹配范围 / 处理图　→　3 应用到整个文件夹'))
        row=QHBoxLayout()
        self.enabled=QCheckBox('启用识别前去水印')
        self.enabled.setChecked(self.cfg['enabled'])
        row.addWidget(self.enabled)
        b=QPushButton('打开样图');b.clicked.connect(self.open_sample);row.addWidget(b)
        b=QPushButton('吸取水印颜色');b.clicked.connect(self.pick);row.addWidget(b)
        b=QPushButton('手动添加颜色');b.clicked.connect(self.add_color);row.addWidget(b)
        b=QPushButton('删除所选颜色');b.clicked.connect(self.delete_color);row.addWidget(b)
        b=QPushButton('适应视图');b.clicked.connect(lambda:(self.before.fit(),self.after.fit()));row.addWidget(b)
        row.addStretch();layout.addLayout(row)
        controls=QHBoxLayout()
        self.colors=QListWidget();self.colors.setMaximumHeight(100);self.colors.setMaximumWidth(230)
        controls.addWidget(self.colors)
        form=QFormLayout()
        self.tolerance=QDoubleSpinBox();self.tolerance.setRange(0,60);self.tolerance.setValue(self.cfg['tolerance']);self.tolerance.setSingleStep(1)
        self.method=QComboBox();self.method.addItem('背景色替换（文档 / 屏幕）','background');self.method.addItem('周围像素修补（复杂背景）','inpaint')
        self.method.setCurrentIndex(self.method.findData(self.cfg['method']))
        self.expand=QSpinBox();self.expand.setRange(0,3);self.expand.setValue(self.cfg['expand'])
        self.protect=QCheckBox('保护深色文字 / 深背景中的亮色文字');self.protect.setChecked(self.cfg['protect_text'])
        self.blend_edges=QCheckBox('清除水印附近的半透明边缘');self.blend_edges.setChecked(self.cfg['blend_edges'])
        self.auto_bg=QCheckBox('每张图片自动估计背景色');self.auto_bg.setChecked(self.cfg['background'] is None)
        self.bg=QPushButton('指定替换背景色（不是水印颜色）');self.bg.clicked.connect(self.choose_background)
        form.addRow('颜色容差',self.tolerance);form.addRow('处理方法',self.method);form.addRow('边缘扩展像素',self.expand)
        controls.addLayout(form)
        other=QVBoxLayout();other.addWidget(self.protect);other.addWidget(self.auto_bg);other.addWidget(self.bg)
        other.addWidget(self.blend_edges)
        self.bg_value=QLabel();other.addWidget(self.bg_value)
        b=QPushButton('恢复自动背景色（推荐）');b.clicked.connect(lambda:self.auto_bg.setChecked(True));other.addWidget(b)
        self.view=QComboBox();self.view.addItems(['红色显示匹配范围','显示去水印处理图']);other.addWidget(self.view)
        self.view.setCurrentIndex(1)
        self.retry_button=QPushButton('重试去水印预览')
        self.retry_button.clicked.connect(self.changed);other.addWidget(self.retry_button)
        controls.addLayout(other);layout.addLayout(controls)
        split=QSplitter();self.before=ImageView();self.after=ImageView();split.addWidget(self.before);split.addWidget(self.after)
        split.setSizes([600,600]);layout.addWidget(split,1)
        layout.addWidget(QLabel('左：来源样图，可滚轮放大后吸色　　右：同尺寸匹配范围 / 去水印预览'))
        self.message=QLabel('原图不修改。同色正文也可能被选中，请先检查红色范围；保护开关不能恢复被遮住的字符。')
        self.message.setWordWrap(True);layout.addWidget(self.message)
        actions=QHBoxLayout()
        for label,action in [('保存设置','save'),('仅此图去水印并识别','one'),('全部去水印并重识别','all')]:
            b=QPushButton(label);b.clicked.connect(lambda checked=False,a=action:self.apply(a));actions.addWidget(b)
        self.export_button=QPushButton('导出最近处理图');self.export_button.clicked.connect(window.export_watermark_images);actions.addWidget(self.export_button)
        b=QPushButton('关闭');b.clicked.connect(self.reject);actions.addWidget(b);layout.addLayout(actions)
        self.before.color_selected.connect(self.picked)
        self.view.currentIndexChanged.connect(self.render_preview)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(250);self.timer.timeout.connect(self.start_preview)
        for widget in (self.enabled,self.protect,self.auto_bg,self.blend_edges):widget.toggled.connect(self.changed)
        for widget in (self.method,):widget.currentIndexChanged.connect(self.changed)
        for widget in (self.tolerance,self.expand):widget.valueChanged.connect(self.changed)
        self.refresh_colors()
        if window.page_id:
            page=window.store.page(window.page_id)
            self.load_sample(Path(page['root'])/page['relative'],page['page'])

    def value(self):
        return settings({**self.cfg,'enabled':self.enabled.isChecked(),'tolerance':self.tolerance.value(),
                         'method':self.method.currentData(),'expand':self.expand.value(),'protect_text':self.protect.isChecked(),'blend_edges':self.blend_edges.isChecked(),
                         'background':None if self.auto_bg.isChecked() else self.cfg.get('background') or [255,255,255]})

    def showEvent(self,event):
        super().showEvent(event)
        QTimer.singleShot(0,lambda:(self.before.fit(),self.after.fit()))

    def load_sample(self,path,page=0):
        try:
            self.image=read_page(path,page)
            self.cleaned,self.mask=None,None
            self.before.load_pixmap(pixmap(self.image),[])
            self.after.load_pixmap(pixmap(self.image),[])
            self.changed()
        except Exception as exc:
            self.message.setText('样图读取失败：'+str(exc))

    def open_sample(self):
        path,_=QFileDialog.getOpenFileName(self,'打开用于取色的样图',self.window.root,'图片 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)')
        if path:self.load_sample(path)

    def pick(self):
        if self.image is not None:
            self.before.pick_color=True
            self.message.setText('在左侧图片中点击水印像素；Esc 取消。可以重复取色以覆盖不同深浅。')

    def picked(self,color):
        if color not in self.cfg['colors'] and len(self.cfg['colors'])<16:
            self.cfg['colors'].append(color)
        self.enabled.setChecked(True)
        self.refresh_colors();self.changed()

    def add_color(self):
        color=QColorDialog.getColor(QColor('#b0b0b0'),self,'添加水印颜色')
        if color.isValid():self.picked([color.red(),color.green(),color.blue()])

    def delete_color(self):
        i=self.colors.currentRow()
        if i>=0:
            self.cfg['colors'].pop(i);self.refresh_colors();self.changed()

    def refresh_colors(self):
        self.colors.clear()
        for c in self.cfg['colors']:
            self.colors.addItem('#%02X%02X%02X   RGB %s'%(*c,str(tuple(c))))
            self.colors.item(self.colors.count()-1).setForeground(QColor(*c))
        if self.colors.count():self.colors.setCurrentRow(self.colors.count()-1)

    def choose_background(self):
        color=QColorDialog.getColor(QColor(*(self.cfg.get('background') or [255,255,255])),self,'选择替换背景色')
        if color.isValid():
            self.cfg['background']=[color.red(),color.green(),color.blue()]
            self.auto_bg.setChecked(False);self.changed()

    def changed(self,*_):
        self.valid_preview=None
        bg=self.cfg.get('background') or [255,255,255]
        self.bg_value.setText('替换为：每张图片自己的背景色' if self.auto_bg.isChecked() else '指定替换色：#%02X%02X%02X（请与图片背景一致）'%tuple(bg))
        self.generation+=1
        self.timer.start()

    def start_preview(self):
        if self.image is None or (self.worker and self.worker.isRunning()) or self.closing is not None:return
        cfg=self.value()
        if cfg['enabled'] and not cfg['colors']:
            self.message.setText('请先吸取或手动添加水印颜色。');return
        self.message.setText('正在生成同尺寸预览…')
        self.worker=Preview(self.image.copy(),cfg,self.generation,self)
        self.worker.ready.connect(self.preview_ready);self.worker.failed.connect(self.preview_failed);self.worker.finished.connect(self.preview_finished)
        self.worker.start()

    def preview_ready(self,generation,cleaned,mask,stats):
        if generation!=self.generation:return
        self.valid_preview=generation
        self.cleaned,self.mask=cleaned,mask
        changed=stats.get('changed_pixels',0)
        if not self.enabled.isChecked():
            message='尚未启用去水印：请吸取水印颜色，或勾选「启用识别前去水印」。'
        elif not changed:
            message='未去除任何像素。请重新吸取水印颜色、调整颜色容差；若水印被文字保护排除，请谨慎调整保护开关。'
        else:
            message=f"预览已改变 {changed:,} 像素（匹配 {stats['fraction']:.2%}）。点击下方「仅此图」或「全部」按钮执行去水印并识别。"
        self.message.setText(message)
        self.render_preview()

    def preview_failed(self,generation,error):
        if generation==self.generation:
            self.valid_preview=None
            self.cleaned,self.mask=None,None
            if self.image is not None:self.after.load_pixmap(pixmap(self.image),[])
            self.message.setText('去水印预览失败：'+error+'；调整设置后点击「重试去水印预览」。')

    def preview_finished(self):
        old=self.worker;self.worker=None
        if old:old.deleteLater()
        if self.closing is not None:
            super().done(self.closing)
        elif old and old.generation!=self.generation:self.timer.start()

    def render_preview(self,*_):
        if self.image is None or self.cleaned is None:return
        if self.view.currentIndex()==1:
            image=self.cleaned
        else:
            from PIL import Image
            tint=Image.blend(self.image.convert('RGB'),Image.new('RGB',self.image.size,'#ff2244'),.65)
            image=Image.composite(tint,self.image,self.mask)
        self.after.load_pixmap(pixmap(image),[])

    def apply(self,action):
        cfg=self.value()
        if action in ('one','all') and not cfg['enabled']:
            self.message.setText('请先勾选「启用识别前去水印」并选择水印颜色。');return
        if cfg['enabled'] and not cfg['colors']:
            self.message.setText('请选择水印颜色后再应用。');return
        if action in ('one','all') and not self.window.root:
            self.message.setText('请先打开需要批量处理的图片文件夹。');return
        if action=='one' and not self.window.page_id:return
        if action in ('one','all') and self.valid_preview!=self.generation:
            self.message.setText('请先完成有效的去水印预览。指定替换色应与图片背景一致，可点击「恢复自动背景色（推荐）」后重试。');return
        self.action=action
        self.window.save_watermark_settings(cfg)
        if action in ('one','all'):self.window.enqueue_watermark_batch(cfg,action=='all')
        self.accept()

    def done(self,result):
        self.timer.stop()
        if self.worker and self.worker.isRunning():
            self.closing=result
            self.message.setText('正在结束预览任务…')
            return
        super().done(result)


class WatermarkMixin:
    def build_watermark_ui(self):
        self.watermark_defaults=settings()
        from .app import button
        actions=QHBoxLayout()
        actions.addWidget(button('颜色去水印',self.watermark_dialog))
        actions.addWidget(button('查看去水印图',self.show_cleaned_image))
        actions.addWidget(QLabel('取色预览后，点击「仅此图」或「全部」执行'))
        actions.addStretch()
        self.centralWidget().layout().insertLayout(2,actions)

    def latest_cleaned_document(self):
        if not self.page_id:return None
        page=self.store.page(self.page_id)
        for version in self.store.versions(self.page_id):
            doc=self.store.version_document(version['id'],original=True)
            path=doc.get('color_watermark',{}).get('cleaned_path')
            if doc.get('fingerprint')==page['fingerprint'] and not doc.get('roi') and path and Path(path).is_file():
                return doc
        return None

    def show_cleaned_image(self):
        doc=self.latest_cleaned_document()
        if not doc:
            self.status_label.setText('当前图还没有去水印结果；点击「颜色去水印」取色后执行。')
            return
        dialog=QDialog(self);dialog.setWindowTitle('最近去水印结果 · 左原图 / 右处理图');dialog.resize(1200,800)
        layout=QVBoxLayout(dialog);split=QSplitter()
        before,after=ImageView(),ImageView();split.addWidget(before);split.addWidget(after);layout.addWidget(split)
        page=self.store.page(self.page_id)
        before.load_pixmap(pixmap(read_page(Path(page['root'])/page['relative'],page['page'])),[])
        after.load(doc['color_watermark']['cleaned_path'],[])
        layout.addWidget(QLabel('显示最近完成的处理图，包括未采纳的 OCR 候选；人工修订保留。'))
        b=QPushButton('关闭');b.clicked.connect(dialog.accept);layout.addWidget(b)
        QTimer.singleShot(0,lambda:(before.fit(),after.fit()))
        dialog.exec()

    def current_watermark_settings(self):
        saved=self.store.watermark_settings(self.root) if self.root else None
        return settings(saved if saved is not None else self.watermark_defaults)

    def save_watermark_settings(self,cfg):
        self.watermark_defaults=settings(cfg)
        if self.root:self.store.save_watermark_settings(self.root,cfg)
        self.save_settings()

    def watermark_dialog(self):
        WatermarkDialog(self).exec()

    def enqueue_watermark_batch(self,cfg,all_images=True):
        self.cancel()
        pages=self.store.pages(self.root) if all_images else [self.store.page(self.page_id)]
        jobs=[]
        for page in pages:
            job=self.job(page,color_watermark=copy.deepcopy(cfg),candidate=bool(page['active_version']))
            self.store.save_request(page['id'],job)
            self.store.status(page['id'],'pending')
            jobs.append(job)
        self.refresh_list()
        self.queue.add(jobs)
        self.status_label.setText(f'已安排 {len(jobs)} 页：去水印后 OCR；未修改稿自动更新，人工修订保留为当前稿，新结果进入候选。')

    def export_watermark_images(self):
        folder=QFileDialog.getExistingDirectory(self,'导出最近去水印处理图（包括尚未采纳的候选）',self.root)
        if folder:
            import time
            target=Path(folder)/'ocr-export'/('cleaned-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uid()[:6])
            try:
                count=export_cleaned(self.store,self.root,target)
                QMessageBox.information(self,'导出处理图',f'已导出 {count} 页到 {target}')
            except Exception as exc:QMessageBox.warning(self,'导出失败',str(exc))

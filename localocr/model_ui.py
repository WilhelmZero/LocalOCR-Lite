import importlib.util
import os
import sys
from collections import deque
from PySide6.QtCore import QProcess,QProcessEnvironment
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,QPlainTextEdit,QCheckBox
from .model_setup import FEATURES,installation_commands

class ModelDialog(QDialog):
    def __init__(self,window):
        super().__init__(window)
        self.window=window;self.pending=deque();self.process=None
        self.setWindowTitle('按需安装与模型下载');self.resize(820,620)
        layout=QVBoxLayout(self)
        note=QLabel('首次使用只下载所选功能。基础文字与代码共用模型；表格、VL 单独安装。\n依赖与模型可能占用数百 MB 至数 GB。只下载公开文件，不上传图片。\n选择 CPU / GPU 会切换本安装目录的 Paddle 运行环境；失败可重试。')
        note.setWordWrap(True);layout.addWidget(note)
        self.feature=QComboBox()
        for key,label in FEATURES.items():self.feature.addItem(label,key)
        layout.addWidget(self.feature)
        self.device=QComboBox();self.device.addItem('CPU（默认 / Mac）','cpu')
        if sys.platform=='win32':self.device.addItem('NVIDIA GPU（Windows，可额外下载数 GB）','gpu')
        layout.addWidget(self.device)
        self.dependencies=QCheckBox('同时安装所选功能的运行依赖')
        self.dependencies.setChecked(importlib.util.find_spec('pip') is not None)
        layout.addWidget(self.dependencies)
        self.state=QLabel();layout.addWidget(self.state)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);layout.addWidget(self.log,1)
        row=QHBoxLayout();self.start=QPushButton('下载 / 安装 / 重试');self.start.clicked.connect(self.begin);row.addWidget(self.start)
        b=QPushButton('取消下载');b.clicked.connect(self.cancel);row.addWidget(b)
        b=QPushButton('关闭');b.clicked.connect(self.reject);row.addWidget(b);layout.addLayout(row)
        self.feature.currentIndexChanged.connect(self.refresh);self.refresh()

    def refresh(self):
        record=self.window.data_dir/('model-'+self.feature.currentData()+'.json')
        self.state.setText('已有安装验证记录；缓存文件若被移走需重新验证。' if record.exists() else '尚无安装验证记录；已有模型缓存将由下载器复用。')

    def begin(self):
        if self.process:return
        if self.window.queue.current or self.window.queue.waiting:
            self.state.setText('请先完成或取消 OCR 队列，再安装模型，避免同时修改运行依赖。');return
        try:
            commands=installation_commands(self.feature.currentData(),self.device.currentData())
        except ValueError as exc:self.state.setText(str(exc));return
        self.window.queue.stop_process()
        self.pending=deque(commands if self.dependencies.isChecked() else commands[-1:])
        self.start.setEnabled(False);self.feature.setEnabled(False);self.device.setEnabled(False);self.dependencies.setEnabled(False)
        self.log.clear();self.next()

    def next(self):
        if not self.pending:
            self.unlock();self.state.setText('完成；运行环境和所选模型已通过本机推理检查。');return
        args=self.pending.popleft();self.log.appendPlainText('> Python '+' '.join(args))
        process=QProcess(self);self.process=process
        process.setProgram(os.environ.get('LOCAL_OCR_PYTHON',sys.executable));process.setArguments(args)
        from pathlib import Path
        process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        env=QProcessEnvironment.systemEnvironment();env.insert('PYTHONUTF8','1');env.insert('PYTHONIOENCODING','utf-8')
        env.insert('LOCAL_OCR_DATA',str(self.window.data_dir));process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda:self.log.appendPlainText(bytes(process.readAllStandardOutput()).decode('utf-8',errors='replace')))
        process.finished.connect(self.finished)
        process.errorOccurred.connect(lambda error:self.finished(-1) if error==QProcess.ProcessError.FailedToStart else None)
        self.state.setText('正在安装 / 下载，详细进度见下方日志…');process.start()

    def finished(self,code,*_):
        if not self.process:return
        self.process.deleteLater();self.process=None
        if code:
            self.pending.clear();self.unlock();self.state.setText('失败，详细原因见日志；修改网络或依赖后可点击重试。')
        else:self.next()

    def unlock(self):
        for widget in (self.start,self.feature,self.device,self.dependencies):widget.setEnabled(True)

    def cancel(self):
        self.pending.clear()
        if self.process:
            process=self.process;self.process=None;process.blockSignals(True);process.kill();process.waitForFinished(3000);process.deleteLater()
        self.unlock();self.state.setText('已取消；下载器会在重试时复用可用缓存。')

    def done(self,result):
        self.cancel();super().done(result)

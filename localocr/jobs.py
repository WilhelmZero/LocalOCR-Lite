from __future__ import annotations

import json
import os
import sys
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, Signal, QTimer

from .core import scan_folder


class Scanner(QThread):
    completed = Signal(str, list)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self):
        try:
            entries = []
            for entry in scan_folder(self.root, self.isInterruptionRequested):
                entries.append(entry)
                if len(entries) % 25 == 0:
                    self.progress.emit(len(entries))
            if not self.isInterruptionRequested():
                self.completed.emit(self.root, entries)
        except Exception as exc:
            self.failed.emit(str(exc))


class Queue(QObject):
    started = Signal(dict)
    completed = Signal(dict, dict)
    failed = Signal(dict, str)
    changed = Signal()
    log = Signal(str)

    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.waiting, self.current, self.paused = deque(), None, False
        self.process, self.buffer, self.offline = None, b"", None

    def add(self, jobs):
        existing = {j["page_id"] for j in self.waiting}
        if self.current:
            existing.add(self.current["page_id"])
        for job in jobs:
            if job["page_id"] not in existing:
                self.waiting.append(job)
                existing.add(job["page_id"])
        self.changed.emit()
        self.next()

    def next(self):
        if self.current or self.paused or not self.waiting:
            return
        self.current = self.waiting.popleft()
        job = self.current
        if self.process is None or self.offline != job["offline"]:
            self.stop_process()
            process = QProcess(self)
            self.process = process
            self.offline = job["offline"]
            env = QProcessEnvironment.systemEnvironment()
            env.insert("PYTHONUTF8", "1")
            env.insert("PYTHONIOENCODING", "utf-8")
            process.setProcessEnvironment(env)
            runtime = os.environ.get("LOCAL_OCR_PYTHON", sys.executable)
            process.setProgram(runtime)
            if getattr(sys, "frozen", False) and runtime == sys.executable:
                process.setArguments(["--worker"])
            else:
                process.setArguments(["-u", "-m", "localocr.worker"])
            process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
            process.readyReadStandardOutput.connect(self.read_output)
            process.readyReadStandardError.connect(self.read_error)
            process.finished.connect(self.exited)
            process.errorOccurred.connect(self.process_error)
            process.started.connect(self.send)
            process.start()
        else:
            self.send()
        self.started.emit(job)
        self.changed.emit()

    def retry(self, job):
        """Replace this page's queued/running request without losing other pages."""
        self.waiting = deque(j for j in self.waiting if j['page_id'] != job['page_id'])
        if self.current and self.current['page_id'] == job['page_id']:
            self.current = None
            self.stop_process()
        self.waiting.appendleft(job)
        self.paused = False
        self.changed.emit()
        self.next()

    def send(self):
        if self.current and self.process:
            self.process.write((json.dumps(self.current, ensure_ascii=False)+"\n").encode("utf-8"))

    def read_error(self):
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            path = self.data_dir / "logs"
            path.mkdir(exist_ok=True)
            with (path / "worker.log").open("a", encoding="utf-8") as f:
                f.write(text)
            self.log.emit(text[-4000:])

    def read_output(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            marker = line.find(b"@@OCR@@")
            if marker < 0:
                self.log.emit(line.decode("utf-8", errors="replace"))
                continue
            try:
                result = json.loads(line[marker+7:])
            except ValueError:
                continue
            if self.current and result.get("job_id") == self.current["job_id"]:
                job, self.current = self.current, None
                if "error" in result:
                    self.failed.emit(job, result["error"])
                else:
                    self.completed.emit(job, result["result"])
                self.changed.emit()
                QTimer.singleShot(0, self.next)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.exited(-1, QProcess.ExitStatus.CrashExit)

    def exited(self, code, status):
        job, self.current = self.current, None
        if self.process:
            self.process.deleteLater()
            self.process = None
        if job:
            self.failed.emit(job, f"识别进程退出（代码 {code}），请查看日志或使用 CPU 重试")
        self.changed.emit()
        QTimer.singleShot(0, self.next)

    def stop_process(self):
        if self.process:
            process, self.process = self.process, None
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(3000)
            process.deleteLater()
        self.buffer = b""

    def cancel(self):
        jobs = list(self.waiting) + ([self.current] if self.current else [])
        self.waiting.clear()
        self.current = None
        self.stop_process()
        self.paused = False
        self.changed.emit()
        return jobs

    def toggle_pause(self):
        self.paused = not self.paused
        self.changed.emit()
        self.next()

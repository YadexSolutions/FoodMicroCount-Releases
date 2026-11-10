"""Thread-pool jobs with cooperative cancellation between work units."""
import threading
from PySide6.QtCore import QObject, Signal, QRunnable


class TaskSignals(QObject):
    result=Signal(object)
    error=Signal(str)
    finished=Signal()
    progress=Signal(int,str)


class Task(QRunnable):
    def __init__(self,fn):
        super().__init__();self.fn=fn;self.signals=TaskSignals();self.cancelled=threading.Event()
    def run(self):
        try:
            result=self.fn(self.cancelled,self.signals.progress.emit)
            if not self.cancelled.is_set():self.signals.result.emit(result)
        except Exception as e:self.signals.error.emit(str(e))
        finally:self.signals.finished.emit()

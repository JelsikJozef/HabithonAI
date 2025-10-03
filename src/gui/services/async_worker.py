from __future__ import annotations

"""Qt background worker with live log/print forwarding.

Use to run long tasks without freezing the UI and stream logs to a QTextEdit.

Example usage from a view:

    from ..services.async_worker import start_worker

    def _on_click(self):
        cfg = {...}
        def job(progress=None, should_cancel=None):
            if should_cancel and should_cancel():
                return {"cancelled": True}
            progress and progress("Starting ...")
            res = self.svc.convert_run(cfg, progress=progress, should_cancel=should_cancel)
            if should_cancel and should_cancel():
                return {"cancelled": True, "partial": res}
            progress and progress("Finished convert.")
            return res
        self._thread, self._worker = start_worker(
            job,
            on_log=self._append_log_line,
            on_result=self._handle_result,
            on_error=self._handle_error,
            on_started=lambda: self._set_busy(True),
            on_finished=lambda: self._set_busy(False),
        )

The worker also installs a temporary logging.Handler to forward Python logs
and redirects stdout/stderr to emit "print" output as log lines.
"""

import logging
import threading
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any, Callable

from ..views.qt import QObject, Signal, QThread


class _QtLogHandler(logging.Handler):
    """Logging handler that forwards records via a Qt signal."""

    def __init__(self, emit_fn: Callable[[str], None], level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._emit = emit_fn

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - defensive
            msg = record.getMessage()
        try:
            self._emit(msg)
        except Exception:
            pass


class _StreamProxy(StringIO):
    """A text stream that forwards complete lines via a callback."""

    def __init__(self, emit_fn: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit_fn
        self._buf = []
        self._lock = threading.Lock()

    def write(self, s: str) -> int:  # type: ignore[override]
        with self._lock:
            self._buf.append(s)
            joined = "".join(self._buf)
            lines = joined.splitlines(keepends=True)
            # Emit all full lines
            to_keep: list[str] = []
            if lines and not lines[-1].endswith(("\n", "\r")):
                to_keep.append(lines.pop())
            for ln in lines:
                self._emit(ln.rstrip("\r\n"))
            self._buf = to_keep
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        with self._lock:
            if self._buf:
                s = "".join(self._buf)
                if s:
                    self._emit(s)
                self._buf = []


class BackgroundWorker(QObject):
    started = Signal()
    log = Signal(str)  # emits log lines
    finished = Signal(object)  # emits result payload
    error = Signal(str)  # emits error message

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._thread: QThread | None = None
        self._cancel_event = threading.Event()
        # We expose emitters for the job closure
        self.emit_log = lambda s: self.log.emit(str(s))

    def attach_to(self, thread: QThread) -> None:
        self._thread = thread
        self.moveToThread(thread)

    # --- Cancellation ---
    def cancel(self) -> None:
        self._cancel_event.set()
        try:
            self.emit_log("Cancel requested…")
        except Exception:
            pass

    def should_cancel(self) -> bool:
        return self._cancel_event.is_set()

    # --- Internal context for log forwarding ---
    @contextmanager
    def _forward_logs(self):
        handler = _QtLogHandler(self.emit_log, level=logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        # Attach to root so all modules bubble up
        root = logging.getLogger()
        root.addHandler(handler)
        # stdout/stderr capture
        out_proxy = _StreamProxy(self.emit_log)
        err_proxy = _StreamProxy(self.emit_log)
        try:
            with redirect_stdout(out_proxy), redirect_stderr(err_proxy):
                yield
        finally:
            try:
                out_proxy.flush()
                err_proxy.flush()
            except Exception:
                pass
            root.removeHandler(handler)

    # --- Execution entry point ---
    def run(self) -> None:  # slot connected to QThread.started
        self.started.emit()
        try:
            with self._forward_logs():
                # Attempt to pass a progress and cancel callback to the callable
                try:
                    result = self._fn(
                        *self._args,
                        progress=self.emit_log,
                        should_cancel=self.should_cancel,
                        **self._kwargs,
                    )
                except TypeError:
                    # Fallback when callable doesn't accept these kwargs
                    result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as e:  # pragma: no cover - runtime safety
            self.error.emit(str(e))


class _UiBridge(QObject):
    """Lives in the main thread; re-emits signals so UI callbacks run on GUI thread.

    This avoids invoking QWidget methods from the worker thread, which can crash Qt.
    """

    started = Signal()
    log = Signal(str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()


def start_worker(
    fn: Callable[..., Any],
    *args: Any,
    on_log: Callable[[str], None] | None = None,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_started: Callable[[], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    **kwargs: Any,
) -> tuple[QThread, BackgroundWorker]:
    """Create and start a background worker for callable `fn`.

    Returns (thread, worker). Caller should keep references to avoid GC.
    """
    thread = QThread()
    worker = BackgroundWorker(fn, *args, **kwargs)
    worker.attach_to(thread)

    # Bridge signals to main thread
    bridge = _UiBridge()  # created in caller's (main) thread
    try:
        from ..views.qt import QApplication as _QApp  # type: ignore

        app = _QApp.instance()
        if app is not None:
            bridge.setParent(app)  # ensure lifetime tied to app
    except Exception:
        pass
    # Keep a Python reference via the worker to avoid GC
    try:
        worker._bridge = bridge  # type: ignore[attr-defined]
    except Exception:
        pass

    # Wire worker -> bridge (queued across threads)
    worker.started.connect(bridge.started)  # type: ignore[arg-type]
    worker.log.connect(bridge.log)  # type: ignore[arg-type]
    worker.finished.connect(bridge.finished)  # type: ignore[arg-type]
    worker.error.connect(bridge.error)  # type: ignore[arg-type]

    # Wire bridge -> callbacks (executed on GUI thread)
    if on_started:
        bridge.started.connect(on_started)  # type: ignore[arg-type]
    if on_log:
        bridge.log.connect(on_log)  # type: ignore[arg-type]
    if on_result:
        bridge.finished.connect(on_result)  # type: ignore[arg-type]
    if on_error:
        bridge.error.connect(on_error)  # type: ignore[arg-type]

    def _cleanup(*_args: Any) -> None:
        if on_finished:
            on_finished()
        try:
            thread.quit()
            thread.wait()
        except Exception:
            pass

    # Ensure cleanup when either result or error happens
    bridge.finished.connect(_cleanup)  # type: ignore[arg-type]
    bridge.error.connect(_cleanup)  # type: ignore[arg-type]

    # Kick off
    thread.started.connect(worker.run)  # type: ignore[arg-type]
    thread.start()
    return thread, worker

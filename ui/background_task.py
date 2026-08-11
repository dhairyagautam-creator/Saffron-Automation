"""Run a blocking function on a background thread without freezing the Tk
main thread -- the same shape already used by
`ui/operations_page.py::_start_automatic_send` (thread + `widget.after(0,
...)` push), pulled out into one reusable helper so every future
long-running-with-progress upload/pipeline step doesn't reinvent it.
"""

import threading


def run_in_background(widget, work_fn, on_progress=None, on_done=None) -> None:
    """Runs `work_fn(report_progress)` on a daemon thread.

    `work_fn` must accept a single `report_progress(percent, message)`
    callable and return a result (or raise). Progress updates and the
    final result/exception are marshalled onto the main Tk thread via
    `widget.after(0, ...)` -- never called directly from the worker
    thread, since Tkinter widgets aren't thread-safe.

    `on_progress(percent, message)` is called for every progress update.
    `on_done(result, error)` is called exactly once at the end -- `error`
    is the caught exception (or None on success), mirroring the
    try/except-in-worker pattern already used for the email-send thread.

    `widget.after(...)` itself raises RuntimeError if the app is closed
    (window/Tk interpreter destroyed) while this background thread is
    still running -- e.g. a user quitting mid-upload, or mid-heat-map-load
    on Analytics Dashboard's first open. That's an ordinary shutdown race,
    not a bug to surface: the callback below is swallowed rather than left
    as an unhandled exception on a daemon thread (which would otherwise
    print a traceback with no console to show it in on a windowed build).
    """
    def _schedule(callback) -> None:
        try:
            widget.after(0, callback)
        except RuntimeError:
            pass  # window closed while the background task was still running

    def report_progress(percent, message) -> None:
        if on_progress is not None:
            _schedule(lambda: on_progress(percent, message))

    def worker() -> None:
        try:
            result = work_fn(report_progress)
        except Exception as exc:
            if on_done is not None:
                # `exc` MUST be captured as a default argument here, not
                # referenced free in the closure: Python deletes the name
                # bound by `except ... as exc` the moment this except block
                # exits (PEP 3110, to avoid a traceback reference cycle) --
                # by the time Tkinter actually invokes this lambda (queued
                # via `.after(0, ...)`, running well after this function has
                # already returned), a free-variable reference to `exc`
                # raises "NameError: cannot access free variable 'exc'",
                # which then masked the REAL exception `work_fn` raised
                # behind a confusing, unrelated-looking generic error
                # dialog -- and left every caller's own `on_done` error
                # handling (which would otherwise hide its loading overlay)
                # never actually running, so the UI looked permanently
                # stuck. Binding as a default argument copies the value in
                # at lambda-creation time, before `exc` is ever deleted.
                _schedule(lambda exc=exc: on_done(None, exc))
        else:
            if on_done is not None:
                _schedule(lambda: on_done(result, None))

    threading.Thread(target=worker, daemon=True).start()

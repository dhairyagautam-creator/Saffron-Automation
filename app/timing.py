"""Tiny timing-log helper shared by the analysis/email pipeline, so each
named phase's duration shows up in the log as `[TIMING] <label>: N.NNs`.
"""

import threading
import time
from contextlib import contextmanager

from loguru import logger


@contextmanager
def timed(label: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.info(f"[TIMING] {label}: {time.perf_counter() - start:.2f}s")


class PhaseTimer:
    """Accumulates elapsed time across several `with timer:` spans that are
    interleaved with other, separately-timed work inside the same loop
    (e.g. hierarchy lookup and email lookup happen back-to-back per finding,
    but should be logged as two separate totals). Call `.log(label)` once
    after the loop finishes."""

    def __init__(self):
        self.total = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info):
        self.total += time.perf_counter() - self._start

    def log(self, label: str) -> None:
        logger.info(f"[TIMING] {label}: {self.total:.2f}s")


class PipelineTimingReport:
    """Collects the measured (never estimated) duration of every named stage
    across one full Run Analysis, from workbook load through email send.

    Stages run on both the main UI thread (import/metrics/rules) and the
    background send thread (hospital suppression/geocoding/SMTP) — a lock
    keeps concurrent `.timed()` calls from corrupting the shared dict.

    `.timed(label)` logs a `[STAGE START]` line on entry and a `[TIMING]`
    line with the measured duration on exit, so a stage that hangs is
    immediately identifiable in the log by its start line with no matching
    finish. Calling `.timed()` twice with the same label (e.g. a stage that
    legitimately runs in two separate spans) accumulates into one total
    rather than overwriting it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stages: dict[str, float] = {}
        self._order: list[str] = []

    def record(self, label: str, seconds: float) -> None:
        with self._lock:
            if label not in self.stages:
                self._order.append(label)
                self.stages[label] = 0.0
            self.stages[label] += seconds

    @contextmanager
    def timed(self, label: str):
        logger.info(f"[STAGE START] {label}")
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.record(label, elapsed)
            logger.info(f"[TIMING] {label}: {elapsed:.2f}s")

    def stages_snapshot(self) -> dict[str, float]:
        """A thread-safe copy of {label: seconds}, safe to read from the UI
        thread while a background thread may still be calling .timed()."""
        with self._lock:
            return dict(self.stages)

    def summary_lines(self) -> list[str]:
        stages = self.stages_snapshot()
        total = sum(stages.values()) or 1.0
        ordered = sorted(stages.items(), key=lambda kv: -kv[1])
        lines = [f"{label}: {seconds:.2f}s ({seconds / total * 100:.0f}%)" for label, seconds in ordered]
        lines.append(f"TOTAL: {sum(stages.values()):.2f}s")
        return lines

    def log_summary(self) -> None:
        logger.info("[TIMING SUMMARY]")
        for line in self.summary_lines():
            logger.info(f"  {line}")


# One report per Run Analysis, reset at the start of each run (see
# ui/operations_page.py) and read from anywhere in the pipeline via
# get_current_report() — the same module-level-singleton pattern already
# used by app/send_state.py for in-progress tracking. Starts non-None so
# any stage instrumented with get_current_report().timed(...) never needs a
# None-check, even if called outside a full Run Analysis (e.g. ad-hoc
# scripts/tests).
_current_report = PipelineTimingReport()


def start_new_report() -> PipelineTimingReport:
    """Begin a fresh report for a new Run Analysis, discarding the previous
    one. Returns the new report so a caller can hold a direct reference."""
    global _current_report
    _current_report = PipelineTimingReport()
    return _current_report


def get_current_report() -> PipelineTimingReport:
    return _current_report

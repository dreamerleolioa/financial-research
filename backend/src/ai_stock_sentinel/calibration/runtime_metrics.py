"""Process memory checkpoints for validation, without logging source payloads."""
from contextlib import contextmanager
from datetime import date
import logging
from pathlib import Path
import os
import resource
import sys
from time import monotonic
from uuid import uuid4


def _memory_bytes() -> tuple[int | None, int | None]:
    rss = peak = None
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != "darwin":
            peak *= 1024
        # Linux exposes current RSS; ru_maxrss alone is a lifetime high-water mark.
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    return rss, peak


class ForwardValidationMetrics:
    def __init__(self, logger: logging.Logger, *, as_of_date: date):
        self.logger = logger
        self.run_id = uuid4().hex
        self.as_of_date = as_of_date.isoformat()
        self.counts: dict[str, int] = {}

    def _log(self, stage: str, event: str, elapsed: float, counts: dict[str, int]) -> None:
        rss, peak = _memory_bytes()
        self.logger.info(
            "Forward validation run=%s as_of_date=%s stage=%s event=%s "
            "rss_bytes=%s peak_rss_bytes=%s elapsed_seconds=%.3f counts=%s",
            self.run_id, self.as_of_date, stage, event, rss, peak, elapsed, counts,
            extra={"validation_run_id": self.run_id, "validation_stage": stage,
                   "stage_event": event, "rss_bytes": rss, "peak_rss_bytes": peak},
        )

    @contextmanager
    def stage(self, name: str, **counts: int):
        started = monotonic()
        self._log(name, "started", 0.0, self.counts | counts)
        try:
            yield
        except BaseException:
            self._log(name, "failed", monotonic() - started, self.counts | counts)
            raise
        else:
            self._log(name, "completed", monotonic() - started, self.counts | counts)

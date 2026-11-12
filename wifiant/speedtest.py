"""Throughput probe.

The radio's "bitrate" is the negotiated PHY rate - it says nothing about what
actually gets through. To see the real number we have to move bytes. The
default target is Cloudflare's speed endpoint because it's plain HTTP, needs no
tooling, and has nodes everywhere.

Downloads are sized so a probe takes a couple of seconds. Nothing is written to
disk; the stream is counted and dropped.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes={size}"
DEFAULT_SIZE = 25_000_000  # 25 MB - big enough for slow links to register
UA = "wifiant/0.1 (throughput probe)"


@dataclass
class SpeedResult:
    mbps: float = 0.0
    bytes: int = 0
    elapsed: float = 0.0
    ttfb: float = 0.0
    url: str = ""
    error: str = ""
    at: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error and self.bytes > 0


class ThroughputProbe:
    """Runs one download in a thread. ``poll()`` for the live rate while it
    runs, ``result`` when it's done."""

    def __init__(self, url: str | None = None, size: int = DEFAULT_SIZE,
                 time_budget: float = 8.0):
        self.url = (url or DEFAULT_URL).format(size=size)
        self.time_budget = time_budget
        self.result = SpeedResult(url=self.url)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bytes_seen = 0
        self._started = 0.0
        self._done = threading.Event()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "ThroughputProbe":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return not self._done.is_set()

    def cancel(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> SpeedResult:
        self._done.wait(timeout)
        return self.result

    def live_mbps(self) -> float:
        elapsed = time.monotonic() - self._started if self._started else 0.0
        if elapsed <= 0.15:
            return 0.0
        return self._bytes_seen * 8 / elapsed / 1e6

    # -- worker ----------------------------------------------------------
    def _run(self) -> None:
        req = urllib.request.Request(self.url, headers={"User-Agent": UA})
        started = time.monotonic()
        self._started = started
        first_byte = 0.0
        total = 0
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                while True:
                    if self._stop.is_set() or time.monotonic() - started > self.time_budget:
                        break
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if not first_byte:
                        first_byte = time.monotonic()
                    total += len(chunk)
                    self._bytes_seen = total
            elapsed = max(1e-6, (first_byte or started) - started + (
                time.monotonic() - (first_byte or started)))
            self.result = SpeedResult(
                mbps=self._bytes_seen * 8 / max(1e-6, time.monotonic() - (first_byte or started)) / 1e6
                if first_byte else 0.0,
                bytes=total,
                elapsed=time.monotonic() - started,
                ttfb=(first_byte - started) if first_byte else 0.0,
                url=self.url,
                at=time.time(),
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.result = SpeedResult(
                url=self.url, error=str(exc), elapsed=time.monotonic() - started,
                bytes=total, at=time.time(),
            )
        finally:
            self._done.set()


def quick_probe(url: str | None = None, size: int = 5_000_000,
                time_budget: float = 4.0) -> SpeedResult:
    """Blocking one-shot, used by ``wifiant speed``."""
    return ThroughputProbe(url=url, size=size, time_budget=time_budget).start().wait(
        time_budget + 12
    )

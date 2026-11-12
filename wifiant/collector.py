"""Background sampler.

One thread owns all the slow work (spawning iw, ping). The UI just reads
whatever the collector last produced, which keeps painting at a steady rate
even when a subprocess hangs for a second.
"""

from __future__ import annotations

import threading
import time

from . import mock, radio
from .stats import History, Sample


class Collector:
    def __init__(self, iface: str | None = None, demo: bool = False,
                 interval: float = 1.0, ping_host: str | None = None,
                 ping_every: float = 10.0):
        self.demo = demo
        self.interval = max(0.2, interval)
        self.ping_every = max(2.0, ping_every)
        self._mock = mock.MockRadio() if demo else None
        self.iface = iface or (self._mock.iface if demo else radio.pick_iface())
        self.ping_host = ping_host
        self.history = History()
        self.latency: dict | None = None
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_ping = 0.0
        self.samples_seen = 0
        self.reconnects = 0
        self._was_connected = True

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "Collector":
        self._thread = threading.Thread(target=self._loop, name="wifiant-collector",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- worker ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self._sample_once()
            except Exception as exc:  # never let the sampler die on a bad read
                self.last_error = f"{type(exc).__name__}: {exc}"
            wait = self.interval - (time.monotonic() - t0)
            if wait > 0:
                self._stop.wait(wait)

    def _sample_once(self) -> None:
        if self.demo and self._mock:
            raw = self._mock.read()
        else:
            raw = radio.read_sample(self.iface)

        if not raw.connected:
            # Don't log a fake zero. A gap in the series is honest information:
            # the antenna didn't get worse, the link fell over.
            if self._was_connected:
                self.reconnects += 1
            self._was_connected = False
            self.last_error = f"{self.iface or 'radio'}: no connection"
            return
        self._was_connected = True

        sample = Sample(
            ts=raw.ts,
            signal=raw.signal_dbm if raw.signal_dbm is not None else -100.0,
            noise=raw.noise_dbm,
            snr=raw.snr_db,
            rx=raw.rx_bitrate,
            tx=raw.tx_bitrate,
            chains=tuple(raw.chains),
            retries=raw.tx_retries,
            failed=raw.tx_failed,
            latency=self.latency["avg"] if self.latency else None,
        )
        self.history.add(sample)
        self.samples_seen += 1

        if time.time() >= self._next_ping:
            self._next_ping = time.time() + self.ping_every
            if self.demo and self._mock:
                self.latency = self._mock.ping()
            else:
                host = self.ping_host or radio.gateway() or "1.1.1.1"
                self.latency = radio.ping(host, count=4)

    # -- reporting -------------------------------------------------------
    def summary(self) -> dict:
        h = self.history
        last = h.last
        return {
            "iface": self.iface,
            "samples": self.samples_seen,
            "reconnects": self.reconnects,
            "last": last,
            "now": h.stats(10),
            "min1": h.stats(60),
            "min5": h.stats(300),
            "session": h.stats(None),
            "trend": h.trend(60),
            "stability": h.stability(60),
            "coverage": h.coverage(60, 1.0 / self.interval),
            "latency": self.latency,
            "error": self.last_error,
        }

"""Rolling statistics over signal samples.

A single dBm number tells you almost nothing while you're filing down the
elements - it moves around on its own. What matters is the stable central
tendency over a window, how wide the jitter is, and whether the latest move
you made actually shifted that central tendency by more than the noise.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Stats:
    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    stdev: float = 0.0
    p10: float = 0.0
    p90: float = 0.0
    lo: float = 0.0
    hi: float = 0.0

    @property
    def jitter(self) -> float:
        """Peak-to-peak spread. Rough proxy for link stability."""
        return self.hi - self.lo


@dataclass
class Sample:
    ts: float
    signal: float
    noise: float | None = None
    snr: float | None = None
    rx: float | None = None
    tx: float | None = None
    chains: tuple[float, ...] = ()
    retries: int | None = None
    failed: int | None = None
    latency: float | None = None

    @property
    def quality(self) -> float:
        """0..100 link score. Signal dominates, SNR and latency refine it.

        Not a vendor score - just a monotonic number that makes "is this
        version better" answerable at a glance.
        """
        base = _clamp((self.signal + 92) / 42, 0.0, 1.0)
        snr = self.snr
        if snr is not None:
            base = 0.75 * base + 0.25 * _clamp(snr / 45.0, 0.0, 1.0)
        if self.latency is not None:
            base *= 1.0 - 0.12 * _clamp((self.latency - 15) / 85.0, 0.0, 1.0)
        return base * 100


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class History:
    """Bounded ring of samples plus the derivations the UI needs."""

    capacity: int = 7200
    samples: deque[Sample] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.samples = deque(self.samples, maxlen=self.capacity)
        self._ewma: float | None = None
        self._last_signal: float | None = None

    # -- ingestion -------------------------------------------------------
    def add(self, sample: Sample) -> None:
        self.samples.append(sample)
        alpha = 0.2
        if self._ewma is None:
            self._ewma = sample.signal
        else:
            self._ewma = alpha * sample.signal + (1 - alpha) * self._ewma

    @property
    def ewma(self) -> float | None:
        return self._ewma

    @property
    def last(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    @property
    def span(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].ts - self.samples[0].ts

    def clear(self) -> None:
        self.samples.clear()
        self._ewma = None

    # -- queries ---------------------------------------------------------
    def window(self, seconds: float) -> list[Sample]:
        if not self.samples:
            return []
        cutoff = self.samples[-1].ts - seconds
        # deque is time-ordered; walk back and stop as soon as we're past it
        out = []
        for s in reversed(self.samples):
            if s.ts < cutoff:
                break
            out.append(s)
        out.reverse()
        return out

    def values(self, seconds: float | None = None, attr: str = "signal") -> list[float]:
        src = self.window(seconds) if seconds else list(self.samples)
        out = []
        for s in src:
            v = getattr(s, attr)
            if v is not None:
                out.append(float(v))
        return out

    def stats(self, seconds: float | None = None, attr: str = "signal") -> Stats:
        vals = self.values(seconds, attr)
        if not vals:
            return Stats()
        ordered = sorted(vals)
        n = len(ordered)
        return Stats(
            count=n,
            mean=statistics.fmean(vals),
            median=statistics.median(vals),
            stdev=statistics.pstdev(vals) if n > 1 else 0.0,
            p10=_quantile(ordered, 0.10),
            p90=_quantile(ordered, 0.90),
            lo=ordered[0],
            hi=ordered[-1],
        )

    def best(self, seconds: float | None = None) -> Sample | None:
        src = self.window(seconds) if seconds else list(self.samples)
        if not src:
            return None
        return max(src, key=lambda s: s.signal)

    def trend(self, seconds: float = 30.0) -> float | None:
        """Least-squares slope in dBm per minute over the window."""
        src = self.window(seconds)
        if len(src) < 6:
            return None
        t0 = src[0].ts
        xs = [s.ts - t0 for s in src]
        ys = [s.signal for s in src]
        mx = statistics.fmean(xs)
        my = statistics.fmean(ys)
        denom = sum((x - mx) ** 2 for x in xs)
        if denom <= 1e-9:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        return slope * 60.0

    def anomalies(self, seconds: float = 3600.0, threshold: float = 4.0) -> list[Sample]:
        """Samples far off the baseline, in robust-sigma units.

        Robust because a fade dip would otherwise drag a plain mean and hide
        itself. Median + MAD is what you want for a signal that spends most of
        its time parked on one value.
        """
        src = self.window(seconds) if self.samples else []
        vals = [s.signal for s in src]
        if len(vals) < 20:
            return []
        med = statistics.median(vals)
        devs = [abs(v - med) for v in vals]
        mad = statistics.median(devs) or 0.5
        sigma = 1.4826 * mad
        if sigma <= 1e-6:
            return []
        return [s for s in src if abs(s.signal - med) / sigma > threshold]

    def stability(self, seconds: float = 60.0) -> float:
        """Share of samples within +/-1.5 dB of the window median, 0..1."""
        src = self.window(seconds)
        if len(src) < 4:
            return 0.0
        vals = [s.signal for s in src]
        med = statistics.median(vals)
        inside = sum(1 for v in vals if abs(v - med) <= 1.5)
        return inside / len(vals)

    def coverage(self, seconds: float = 60.0, expected_hz: float = 1.0) -> float:
        """How much of the window actually had samples, 0..1.

        When the radio disconnects the collector stops appending, so a gap
        shows up here instead of silently making the average look cleaner.
        """
        if len(self.samples) < 2:
            return 0.0
        expected = max(1.0, seconds * expected_hz)
        got = len(self.window(seconds))
        return min(1.0, got / expected)

    def delta_vs(self, other: History, seconds: float = 60.0) -> dict:
        """Compare this history's signal to another's in the same window
        length. Returns the numbers that decide "is the new antenna better"."""
        a = self.stats(seconds)
        b = other.stats(seconds)
        if not a.count or not b.count:
            return {}
        # pooled sigma so the delta can be judged against the noise floor
        pooled = math.sqrt((a.stdev ** 2 + b.stdev ** 2) / 2) or 1e-9
        return {
            "delta_mean": a.mean - b.mean,
            "delta_median": a.median - b.median,
            "sigmas": abs(a.mean - b.mean) / pooled,
            "a": a,
            "b": b,
        }


def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def now() -> float:
    return time.time()

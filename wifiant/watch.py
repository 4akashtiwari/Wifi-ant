"""Long-run logging, for seeing how a link behaves over a day.

`wifiant record` is for a session at the bench. This is the other scale: sample
for hours into a day file, then ask questions of the result - when is the noise
floor lowest, does my signal sag in the afternoon when the neighbours get home,
what does a typical night look like.
"""

from __future__ import annotations

import json
import signal
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .designs import data_home


def watch_dir() -> Path:
    d = data_home() / "watch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _day_path(design: str | None, ts: float) -> Path:
    stamp = time.strftime("%Y%m%d", time.localtime(ts))
    tag = design or "unfiled"
    return watch_dir() / f"{stamp}-{tag}.jsonl"


@dataclass
class DayLog:
    """Append-only jsonl: one line per minute by default.

    Deliberately not one line per sample. A full day at 1 s would be 86k lines
    and tell you nothing extra that a minute mean doesn't.
    """

    design: str | None = None
    interval: float = 60.0
    path: Path | None = None
    rows: list[dict] = field(default_factory=list)

    def append(self, row: dict) -> None:
        target = self.path or _day_path(self.design, row.get("t", time.time()))
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    # -- reading ---------------------------------------------------------
    @staticmethod
    def load(path: Path) -> list[dict]:
        rows = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return rows

    @staticmethod
    def files() -> list[Path]:
        return sorted(watch_dir().glob("*.jsonl"))


def run_watch(collector, design: str | None, interval: float,
              duration: float = 0.0, on_tick=None) -> DayLog:
    """Blocking loop, but with an optional callback so the TUI can reuse it."""
    log = DayLog(design=design, interval=interval)
    stop_at = time.time() + duration if duration else 0.0
    while True:
        if stop_at and time.time() >= stop_at:
            break
        if getattr(collector, "stopped", False):
            break
        win = collector.history.window(interval)
        if win:
            sigs = [s.signal for s in win]
            noises = [s.noise for s in win if s.noise is not None]
            row: dict = {
                "t": round(time.time(), 1),
                "mean": round(statistics.fmean(sigs), 2),
                "lo": min(sigs),
                "hi": max(sigs),
                "n": len(sigs),
            }
            if noises:
                row["noise"] = round(statistics.fmean(noises), 2)
            last = win[-1]
            if last.snr is not None:
                row["snr"] = round(last.snr, 1)
            if collector.iface:
                row["iface"] = collector.iface
            log.append(row)
            log.rows.append(row)
            if on_tick:
                on_tick(row)
        time.sleep(min(interval, 5.0))
    return log


# -- analysis ------------------------------------------------------------
@dataclass
class DayReport:
    hours: dict[int, float]
    noise_by_hour: dict[int, float]
    best_hour: int | None
    worst_hour: int | None
    best_window: tuple[float, float] | None
    spread: float
    total: float
    dips: list[dict]


def _window_sums(rows: list[dict], width: float = 3600.0) -> list[tuple[float, float, float]]:
    """(start, end, mean) over sliding fixed windows, newest last."""
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r["t"])
    t0 = rows[0]["t"]
    buckets: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        buckets[int((r["t"] - t0) // width)].append(r["mean"])
    out = []
    for b in sorted(buckets):
        vals = buckets[b]
        out.append((t0 + b * width, t0 + (b + 1) * width, statistics.fmean(vals)))
    return out


def analyse(rows: list[dict], dip_threshold: float = 6.0,
            dip_steps: int = 3) -> DayReport:
    hours: dict[int, list[float]] = defaultdict(list)
    noises: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        h = time.localtime(r["t"]).tm_hour
        hours[h].append(r["mean"])
        if "noise" in r:
            noises[h].append(r["noise"])

    hour_mean = {h: statistics.fmean(v) for h, v in sorted(hours.items())}
    noise_mean = {h: statistics.fmean(v) for h, v in sorted(noises.items())}

    best_hour = max(hour_mean, key=hour_mean.get) if hour_mean else None
    worst_hour = min(hour_mean, key=hour_mean.get) if hour_mean else None

    windows = _window_sums(rows)
    best_window = None
    if windows:
        s, e, _ = max(windows, key=lambda w: w[2])
        best_window = (s, e)

    vals = [r["mean"] for r in rows]
    spread = (max(vals) - min(vals)) if vals else 0.0

    # dips: a run of consecutive low rows, not a single bad sample
    dips: list[dict] = []
    if vals:
        med = statistics.median(vals)
        run: list[dict] = []
        for r in rows:
            if r["mean"] < med - dip_threshold:
                run.append(r)
            else:
                if len(run) >= dip_steps:
                    dips.append({
                        "start": run[0]["t"],
                        "end": run[-1]["t"],
                        "low": min(x["mean"] for x in run),
                        "steps": len(run),
                    })
                run = []
        if len(run) >= dip_steps:
            dips.append({
                "start": run[0]["t"],
                "end": run[-1]["t"],
                "low": min(x["mean"] for x in run),
                "steps": len(run),
            })

    total_hours = 0.0
    if rows:
        total_hours = (max(r["t"] for r in rows) - min(r["t"] for r in rows)) / 3600

    return DayReport(
        hours=hour_mean,
        noise_by_hour=noise_mean,
        best_hour=best_hour,
        worst_hour=worst_hour,
        best_window=best_window,
        spread=spread,
        total=total_hours,
        dips=dips,
    )


def install_signal_handlers(stop_flag: dict) -> None:
    """SIGINT/SIGTERM set a flag instead of raising, so the day file gets the
    last row and closes cleanly when run under systemd/cron."""

    def _handler(signum, frame):
        stop_flag["stop"] = True

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
    except ValueError:
        pass  # not on the main thread; caller just uses duration

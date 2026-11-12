"""Antenna revision lab book.

One folder per revision under ``$WIFIANT_HOME/designs``. Each folder holds a
``design.json`` (what the version physically is), a free-form ``notes.md`` and
a ``sessions/`` directory with one JSON per measurement run.

It is deliberately plain files - you can rsync it, diff it, or drop it in a git
repo next to your build photos and it behaves like any other source tree.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .stats import History, Sample, Stats


def data_home() -> Path:
    env = os.environ.get("WIFIANT_HOME")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "wifiant"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "v1"


@dataclass
class Design:
    slug: str
    name: str
    description: str = ""
    created: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    geometry: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class SessionMeta:
    """A recorded run. ``series`` is per-second means, relative to start."""

    sid: str
    design: str
    started: float
    ended: float = 0.0
    ssid: str = ""
    bssid: str = ""
    freq_mhz: int = 0
    note: str = ""
    series: list[list[float]] = field(default_factory=list)  # [t, mean, count]

    def values(self, limit_seconds: float | None = None) -> list[float]:
        vals = []
        for t, mean, n in self.series:
            if limit_seconds is not None and t > limit_seconds:
                break
            vals.append(mean)
        return vals

    @property
    def span(self) -> float:
        """Recorded length in seconds."""
        return self.series[-1][0] if self.series else 0.0

    # older name kept because it reads better in a few call sites
    duration = span

    def stats(self, limit_seconds: float | None = None) -> Stats:
        vals = self.values(limit_seconds)
        if not vals:
            return Stats()
        ordered = sorted(vals)
        n = len(ordered)
        return Stats(
            count=n,
            mean=statistics.fmean(vals),
            median=statistics.median(vals),
            stdev=statistics.pstdev(vals) if n > 1 else 0.0,
            p10=ordered[max(0, int(0.10 * (n - 1)))],
            p90=ordered[min(n - 1, int(0.90 * (n - 1)))],
            lo=ordered[0],
            hi=ordered[-1],
        )

    def stability(self, limit_seconds: float | None = None) -> float | None:
        """Share of seconds within ±1.5 dB of the median. None when the run is
        too short for the number to mean anything."""
        vals = self.values(limit_seconds)
        if len(vals) < 4:
            return None
        med = statistics.median(vals)
        return sum(1 for v in vals if abs(v - med) <= 1.5) / len(vals)


class Recorder:
    """Accumulates live samples into a per-second series."""

    def __init__(self, design: str, meta_ssid: str = "", meta_bssid: str = "",
                 freq_mhz: int = 0, every: float = 1.0):
        self.meta = SessionMeta(
            sid=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
            design=design,
            started=time.time(),
            ssid=meta_ssid,
            bssid=meta_bssid,
            freq_mhz=freq_mhz,
        )
        self.every = every
        self._bucket: list[float] = []
        self._label = 0.0
        self._t0 = time.time()
        self._last_ts = 0.0

    def feed(self, sample: Sample) -> None:
        # the UI paints faster than the sampler runs; don't count a sample twice
        if sample.ts <= self._last_ts:
            return
        self._last_ts = sample.ts
        label = (int((sample.ts - self._t0) // self.every)) * self.every
        if self._bucket and label != self._label:
            self._emit()
        if not self._bucket:
            self._label = label
        self._bucket.append(float(sample.signal))

    def _emit(self) -> None:
        if not self._bucket:
            return
        self.meta.series.append([
            round(self._label, 1),
            round(statistics.fmean(self._bucket), 2),
            len(self._bucket),
        ])
        self._bucket = []

    @property
    def elapsed(self) -> float:
        return time.time() - self._t0

    def _view(self) -> list[list[float]]:
        """Committed series plus the bucket still filling, without mutating."""
        series = list(self.meta.series)
        if self._bucket:
            series.append([
                round(self._label, 1),
                round(statistics.fmean(self._bucket), 2),
                len(self._bucket),
            ])
        return series

    def snapshot(self) -> SessionMeta:
        """What the run looks like right now, for the live panel."""
        out = SessionMeta(**{**self.meta.__dict__, "series": self._view()})
        out.ended = time.time()
        return out

    def stop(self, note: str = "") -> SessionMeta:
        self._emit()
        self.meta.ended = time.time()
        self.meta.note = note
        return self.meta


@dataclass
class Comparison:
    """Result of putting two sessions side by side."""

    a: SessionMeta
    b: SessionMeta
    window: float
    a_stats: Stats
    b_stats: Stats
    a_stability: float
    b_stability: float

    @property
    def delta(self) -> float:
        """Median dB shift of B relative to A. Positive = B is better."""
        return self.b_stats.median - self.a_stats.median

    def verdict(self) -> str:
        if not self.a_stats.count or not self.b_stats.count:
            return "not enough data"
        d = self.delta
        pooled = ((self.a_stats.stdev ** 2 + self.b_stats.stdev ** 2) / 2) ** 0.5
        if pooled > 0:
            sig = abs(d) / pooled
        else:
            sig = abs(d) * 10
        if abs(d) < 0.5:
            return f"noise - only {abs(d):.1f} dB apart"
        if sig < 1.0:
            return f"weak - {abs(d):.1f} dB apart, but inside jitter"
        better = "B" if d > 0 else "A"
        return f"{better} wins by {abs(d):.1f} dB ({sig:.1f} sigma)"


class Lab:
    def __init__(self, root: Path | None = None):
        self.root = (root or data_home()) / "designs"
        self.root.mkdir(parents=True, exist_ok=True)

    # -- designs ---------------------------------------------------------
    def designs(self) -> list[Design]:
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            meta = d / "design.json"
            if not meta.exists():
                continue
            try:
                out.append(Design(**json.loads(meta.read_text())))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def path(self, slug: str) -> Path:
        return self.root / slug

    def create(self, name: str, description: str = "", tags: list[str] | None = None,
               geometry: dict | None = None) -> Design:
        base = _slug(name)
        slug, n = base, 2
        while (self.root / slug).exists():
            slug = f"{base}-{n}"
            n += 1
        design = Design(
            slug=slug,
            name=name.strip() or slug,
            description=description,
            tags=tags or [],
            geometry=geometry or {},
        )
        p = self.path(slug)
        (p / "sessions").mkdir(parents=True, exist_ok=True)
        (p / "design.json").write_text(design.to_json())
        (p / "notes.md").write_text(f"# {design.name}\n\n{description}\n")
        return design

    def load(self, slug: str) -> Design | None:
        f = self.path(slug) / "design.json"
        if not f.exists():
            return None
        try:
            return Design(**json.loads(f.read_text()))
        except (json.JSONDecodeError, TypeError):
            return None

    def update(self, design: Design) -> None:
        p = self.path(design.slug)
        p.mkdir(parents=True, exist_ok=True)
        (p / "design.json").write_text(design.to_json())

    def notes_path(self, slug: str) -> Path:
        return self.path(slug) / "notes.md"

    def delete(self, slug: str) -> bool:
        import shutil

        p = self.path(slug)
        if p.is_dir():
            shutil.rmtree(p)
            return True
        return False

    # -- sessions --------------------------------------------------------
    def save_session(self, meta: SessionMeta) -> Path:
        d = self.path(meta.design) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{meta.sid}.json"
        f.write_text(json.dumps(asdict(meta), indent=1))
        return f

    def sessions(self, slug: str | None = None) -> list[SessionMeta]:
        out: list[SessionMeta] = []
        dirs = [self.path(slug)] if slug else [d for d in self.root.iterdir() if d.is_dir()]
        for d in dirs:
            sd = d / "sessions"
            if not sd.is_dir():
                continue
            for f in sorted(sd.glob("*.json")):
                try:
                    out.append(SessionMeta(**json.loads(f.read_text())))
                except (json.JSONDecodeError, TypeError):
                    continue
        out.sort(key=lambda s: s.started)
        return out

    def latest_session(self, slug: str) -> SessionMeta | None:
        got = self.sessions(slug)
        return got[-1] if got else None

    def clear_sessions(self, slug: str) -> int:
        sd = self.path(slug) / "sessions"
        n = 0
        if sd.is_dir():
            for f in sd.glob("*.json"):
                f.unlink()
                n += 1
        return n

    # -- comparison ------------------------------------------------------
    def compare(self, a: SessionMeta, b: SessionMeta,
                window: float | None = None) -> Comparison:
        """Compare two runs. If no window is given, use the shorter run so
        neither side gets an unfair time advantage."""
        if window is None:
            window = min(a.span, b.span) or 1.0
        return Comparison(
            a=a,
            b=b,
            window=window,
            a_stats=a.stats(window),
            b_stats=b.stats(window),
            a_stability=a.stability(window),
            b_stability=b.stability(window),
        )

    def live_vs_session(self, design_slug: str, history: History,
                        seconds: float = 60.0) -> dict | None:
        """Current live window against the best previous run's same-length
        window, so the current build-up is always measured on equal footing."""
        past = [s for s in self.sessions(design_slug) if s.span >= seconds]
        if not past:
            return None
        live = history.stats(seconds)
        if not live.count:
            return None
        best = None
        for s in past:
            st = s.stats(seconds)
            if not st.count:
                continue
            if best is None or st.median > best[1].median:
                best = (s, st)
        if best is None:
            return None
        session, st = best
        return {
            "live": live,
            "session": session,
            "session_stats": st,
            "delta": live.median - st.median,
        }

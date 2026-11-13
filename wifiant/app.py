"""The TUI.

One rule drives the layout: the number you're watching stays in the same place
no matter what else is open, and everything that isn't being watched right now
lives behind a key.

  q  quit      f  focus      d  designs    s  sessions   c  compare
  r  record    x  speed      l  log        a  new        esc  back
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from . import designs as labmod
from . import speedtest
from .chart import plot
from .collector import Collector
from .stats import Sample

FAINT = "dim"


def big_signal(sample: Sample | None, value: str | None = None) -> Text:
    """The one number that matters, colour-coded by usability."""
    if sample is None:
        return Text("  --  dBm", style="dim")
    style = "bold green"
    if sample.signal < -75:
        style = "bold red"
    elif sample.signal < -65:
        style = "bold yellow"
    txt = Text()
    txt.append(value or f"{sample.signal:5.1f}", style=style)
    txt.append(" dBm", style="dim")
    return txt


def arrow(delta: float) -> tuple[str, str]:
    if delta > 0.4:
        return "▲", "green"
    if delta < -0.4:
        return "▼", "red"
    return "=", "yellow"


@dataclass
class Baseline:
    """A finished session used as the reference number in the header."""

    label: str
    design: str
    stats: object
    points: list[tuple[float, float]]  # (seconds since start, dBm)
    span: float


class FocusScreen(ModalScreen):
    """One number, one trace, nothing else."""

    BINDINGS = [Binding("escape", "dismiss", "back")]

    def __init__(self, collector: Collector, baseline: Baseline | None = None,
                 tare: float | None = None):
        super().__init__()
        self.collector = collector
        self.baseline = baseline
        self.tare = tare

    def compose(self) -> ComposeResult:
        with Container(id="focus-box"):
            yield Static("", id="focus-signal")
            yield Static("", id="focus-sub")
            yield Static("", id="focus-chart")
            yield Static("esc back   f/focus leaves this view", id="focus-hint")

    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh_view)
        self.refresh_view()

    def refresh_view(self) -> None:
        if not self.is_mounted:
            return
        h = self.collector.history
        s = self.collector.summary()
        now = s["now"]
        last = h.last

        if last and self.tare is not None:
            d = last.signal - self.tare
            txt = Text()
            txt.append(f"{d:+7.1f}", style="bold green" if d >= 0 else "bold red")
            txt.append(" dB", style=FAINT)
            self.query_one("#focus-signal", Static).update(txt)
        else:
            self.query_one("#focus-signal", Static).update(
                big_signal(last, f"{last.signal:7.1f}" if last else None)
            )

        bits = []
        if now.count:
            bits.append(f"10s avg {now.mean:5.1f}")
            bits.append(f"spread {now.jitter:4.1f}")
        tr = s["trend"]
        if tr is not None:
            word = "rising" if tr > 0.3 else "falling" if tr < -0.3 else "flat"
            bits.append(f"{word} {tr:+.1f} dB/min")
        if self.baseline and self.baseline.stats.count and now.count:
            d = now.mean - self.baseline.stats.mean
            a, _ = arrow(d)
            bits.append(f"{a} {d:+.1f} dB vs {self.baseline.label}")
        self.query_one("#focus-sub", Static).update("    ".join(bits))

        vals = h.values(180)
        chart = plot(vals, width=min(78, self.size.width - 12), height=9,
                     smooth=10)
        self.query_one("#focus-chart", Static).update(chart)


class NewDesignScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("new antenna revision", id="d-title")
            yield Input(placeholder="name, e.g. v3 folded dipole", id="d-name")
            yield Input(placeholder="what changed since the last one", id="d-desc")
            with Horizontal(id="dialog-buttons"):
                yield Button("create", variant="primary", id="d-ok")
                yield Button("cancel", id="d-cancel")

    @on(Button.Pressed, "#d-cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#d-ok")
    def _ok(self) -> None:
        name = self.query_one("#d-name", Input).value.strip()
        if not name:
            self.notify("give it a name first", severity="warning")
            return
        self.dismiss((name, self.query_one("#d-desc", Input).value.strip()))

    def on_input_submitted(self) -> None:
        self._ok()


class PickDesignScreen(ModalScreen):
    """Ctrl-P style picker for revisions."""

    BINDINGS = [Binding("escape", "dismiss", "back")]

    def __init__(self, lab: labmod.Lab, current: str | None):
        super().__init__()
        self.lab = lab
        self.current = current
        self.designs = lab.designs()
        self.index = 0

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("pick a revision", id="d-title")
            yield Static("", id="pick-list")
            yield Static("↑↓ move   enter select   esc back", id="pick-hint")

    def on_mount(self) -> None:
        for i, d in enumerate(self.designs):
            if d.slug == self.current:
                self.index = i
        self._render()

    def _render(self) -> None:
        body = Text()
        for i, d in enumerate(self.designs):
            sessions = self.lab.sessions(d.slug)
            marker = "▸ " if i == self.index else "  "
            style = "bold cyan" if i == self.index else ""
            best = None
            for s in sessions:
                st = s.stats()
                if st.count and (best is None or st.median > best):
                    best = st.median
            body.append(f"{marker}{d.name}", style=style)
            body.append(f"   {len(sessions)} runs", style=FAINT)
            if best is not None:
                body.append(f"   best {best:.1f}", style=FAINT)
            body.append("\n")
        self.query_one("#pick-list", Static).update(body or Text("none yet", style=FAINT))

    def key_down(self) -> None:
        if self.designs:
            self.index = (self.index + 1) % len(self.designs)
            self._render()

    def key_up(self) -> None:
        if self.designs:
            self.index = (self.index - 1) % len(self.designs)
            self._render()

    def key_enter(self) -> None:
        if self.designs:
            self.dismiss(self.designs[self.index].slug)

    def action_dismiss(self, result=None) -> None:  # type: ignore[override]
        self.dismiss(result)


class SessionsScreen(Screen):
    """Every recorded run for a revision, with the option of putting any two
    side by side."""

    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("c", "compare", "compare"),
        Binding("d", "delete", "delete"),
    ]

    def __init__(self, app_ref: "WifiAnt"):
        super().__init__()
        self.app_ref = app_ref
        self.sessions: list[labmod.SessionMeta] = []
        self.chosen: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="sessions-wrap"):
            yield Static("", id="sessions-head")
            yield DataTable(id="sessions-table")
            yield Static("", id="sessions-verdict")
        yield Footer()

    def on_mount(self) -> None:
        self.sessions = self.app_ref.lab.sessions(self.app_ref.design_slug)
        table = self.query_one("#sessions-table", DataTable)
        table.cursor_type = "row"
        table.add_column("session")
        table.add_column("started")
        table.add_column("length")
        table.add_column("median dBm")
        table.add_column("jitter")
        table.add_column("stable")
        table.add_column("note")
        for s in self.sessions:
            st = s.stats()
            table.add_row(
                s.sid[-9:],
                time.strftime("%d %b %H:%M", time.localtime(s.started)),
                f"{s.span:.0f}s",
                f"{st.median:.1f}" if st.count else "--",
                f"{st.stdev:.1f}" if st.count else "--",
                f"{s.stability() * 100:.0f}%" if s.stability() is not None else "--",
                s.note or "",
                key=s.sid,
            )
        self.query_one("#sessions-head", Static).update(
            f"[bold]{self.app_ref.design_slug}[/]  "
            f"{len(self.sessions)} runs\n"
            "[dim]click/enter to mark A, second one marks B, c compares[/]"
        )

    @on(DataTable.RowSelected)
    def _selected(self, event: DataTable.RowSelected) -> None:
        sid = event.row_key.value
        if sid in self.chosen:
            self.chosen.remove(sid)
        else:
            self.chosen.append(sid)
        self.chosen = self.chosen[-2:]
        self.query_one("#sessions-head", Static).update(
            f"[bold]{self.app_ref.design_slug}[/]  "
            f"{len(self.sessions)} runs   marked: "
            + (", ".join(s[-9:] for s in self.chosen) or "none")
        )

    def action_compare(self) -> None:
        if len(self.chosen) < 2:
            self.notify("mark two runs first", severity="warning")
            return
        a, b = self.chosen
        first = next(s for s in self.sessions if s.sid == a)
        second = next(s for s in self.sessions if s.sid == b)
        self.app_ref.push_screen(CompareScreen(self.app_ref.lab, first, second))

    def action_delete(self) -> None:
        table = self.query_one("#sessions-table", DataTable)
        if table.cursor_row is None or not self.sessions:
            return
        idx = table.cursor_row
        if idx >= len(self.sessions):
            return
        target = self.sessions[idx]
        path = self.app_ref.lab.path(target.design) / "sessions" / f"{target.sid}.json"
        if path.exists():
            path.unlink()
        self.sessions.pop(idx)
        table.remove_row(target.sid)
        self.notify(f"deleted {target.sid[-9:]}")

    def action_back(self) -> None:
        self.app.pop_screen()


class CompareScreen(Screen):
    """Two runs, same axes, explicit verdict. This is the screen that decides
    whether a revision survives."""

    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("left", "shrink", "shorter"),
        Binding("right", "grow", "longer"),
    ]

    def __init__(self, lab: labmod.Lab, a: labmod.SessionMeta, b: labmod.SessionMeta):
        super().__init__()
        self.lab = lab
        self.a = a
        self.b = b
        # both runs start at 0, so the honest window is the shorter one -
        # but a run that barely started shouldn't produce a 0 s axis
        self.window = max(5.0, min(a.span, b.span))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="compare-wrap"):
            yield Static("", id="compare-head")
            yield Static("", id="compare-stats")
            yield Static("", id="compare-chart")
            yield Static("", id="compare-legend")
        yield Footer()

    def on_mount(self) -> None:
        self.paint()

    def action_shrink(self) -> None:
        self.window = max(5.0, self.window - 15)
        self.paint()

    @property
    def _max_window(self) -> float:
        return max(5.0, self.a.span, self.b.span)

    def action_grow(self) -> None:
        self.window = min(self._max_window, self.window + 15)
        self.paint()

    def paint(self) -> None:
        cmp = self.lab.compare(self.a, self.b, self.window)
        self.query_one("#compare-head", Static).update(
            f"[bold]A[/] {self.a.design}  {self.a.sid[-9:]}\n"
            f"[bold]B[/] {self.b.design}  {self.b.sid[-9:]}\n"
            f"[dim]window {cmp.window:.0f}s   ←/→ change[/]"
        )
        rows = Text()
        rows.append(f"{'':12}{'mean':>8}{'median':>9}{'jitter':>9}{'stable':>8}\n",
                    style=FAINT)
        for tag, st, stab in ((f"A  {self.a.sid[-6:]}", cmp.a_stats, cmp.a_stability),
                              (f"B  {self.b.sid[-6:]}", cmp.b_stats, cmp.b_stability)):
            rows.append(f"{tag:<12}")
            if st.count:
                stab_txt = f"{stab * 100:6.0f}%" if stab is not None else "    --"
                rows.append(f"{st.mean:8.1f}{st.median:9.1f}{st.stdev:9.1f}"
                            f"{stab_txt:>7}\n")
            else:
                rows.append("        --       --       --      --\n", style=FAINT)
        self.query_one("#compare-stats", Static).update(rows)

        a_vals = self.a.values(self.window)
        b_vals = self.b.values(self.window)
        if a_vals and b_vals:
            xs_a = [p[0] / self.window for p in self.a.series if p[0] <= self.window]
            self.query_one("#compare-chart", Static).update(
                plot(b_vals, width=min(76, self.size.width - 12), height=10,
                     overlay=a_vals, overlay_xs=xs_a,
                     live_style="green", overlay_style="blue")
            )
        verdict = cmp.verdict()
        self.query_one("#compare-legend", Static).update(
            f"[bold]{verdict}[/]\n[dim]green = A   blue = B   yellow = both[/]"
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class WifiAnt(App):
    CSS = """
    Screen { background: $surface; }
    #header { height: 7; padding: 0 1; border: round $primary; }
    #body { height: 1fr; }
    #left { width: 2fr; }
    #right { width: 34; border-left: solid $primary 30%; padding: 0 1; }
    #chart { height: 1fr; border: round $primary 40%; padding: 0 1; }
    #stats { height: 9; border: round $primary 30%; padding: 0 1; }
    #focus-box { align: center middle; width: 100%; height: 100%; }
    #focus-signal { text-align: center; text-style: bold; }
    #focus-sub, #focus-chart, #focus-hint { text-align: center; }
    #focus-hint { dock: bottom; color: $text-disabled; }
    #dialog { width: 64; height: auto; padding: 1 2; border: thick $primary;
              background: $surface; }
    #dialog-buttons { height: 3; align-horizontal: right; }
    #pick-list { height: auto; max-height: 16; }
    #pick-hint { color: $text-disabled; }
    #sessions-wrap, #compare-wrap { padding: 0 2; }
    #sessions-head, #compare-head { height: auto; padding: 1 0; }
    #sessions-table { height: 1fr; }
    #sessions-verdict { height: 2; }
    #compare-stats { height: auto; }
    #compare-chart { height: auto; }
    #compare-legend { height: auto; padding-bottom: 1; }
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("f", "focus", "focus"),
        Binding("d", "designs", "designs"),
        Binding("a", "add_design", "new"),
        Binding("r", "record", "record"),
        Binding("s", "sessions", "sessions"),
        Binding("c", "compare", "compare"),
        Binding("x", "speed", "speedtest"),
        Binding("l", "log", "log"),
        Binding("p", "ping", "ping"),
        Binding("v", "overlay", "overlay"),
        Binding("m", "smooth", "smooth"),
        Binding("t", "tare", "tare"),
        Binding("n", "autorecord", "autorec"),
        Binding("shift+up", "threshold(+5)", "ceiling up", show=False),
        Binding("shift+down", "threshold(-5)", "ceiling down", show=False),
    ]

    def __init__(self, iface: str | None = None, demo: bool = False,
                 interval: float = 1.0, ping_host: str | None = None,
                 lab: labmod.Lab | None = None, design: str | None = None,
                 window: float = 120.0):
        super().__init__()
        self.collector = Collector(iface=iface, demo=demo, interval=interval,
                                   ping_host=ping_host)
        self.lab = lab or labmod.Lab()
        self.design_slug = design
        self.recorder: labmod.Recorder | None = None
        self.recording_started = 0.0
        self.baseline: Baseline | None = None
        self.speed: speedtest.SpeedResult | None = None
        self._probe: speedtest.ThroughputProbe | None = None
        self.window = window
        self.overlay = False
        self.smooth = 10  # trailing moving-average window, samples; 0 = off
        self.headroom = 0.0  # manual y-axis fudge, shift+up/down
        self.tare_value: float | None = None
        self.tare_at: float | None = None
        self.autorecord = False
        self.breach_held = False

    # -- chrome ----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="header"):
            yield Static("", id="headline")
            yield Static("", id="headline-sub")
            yield Static("", id="headline-spark")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Static("", id="chart")
                yield Static("", id="stats")
            with VerticalScroll(id="right"):
                yield Static("", id="right-panel")
        yield Footer()

    def on_mount(self) -> None:
        self.collector.start()
        self.set_interval(1.0, self.paint)
        self.set_interval(5.0, self._autorecord_tick)
        self.refresh_baseline()
        self.title = "WifiAnt"
        self.sub_title = ""
        if self.design_slug:
            d = self.lab.load(self.design_slug)
            self.sub_title = d.name if d else ""
        self.paint()

    def on_unmount(self) -> None:
        self.collector.stop()

    # -- painting --------------------------------------------------------
    def paint(self) -> None:
        if not self.is_mounted:
            return
        s = self.collector.summary()
        h = self.collector.history
        last = s["last"]

        if self.recorder and last is not None:
            self.recorder.feed(last)

        head = Text()
        if last and self.tare_value is not None:
            # measured against where you zeroed it, so a trim shows up as
            # "+0.6 dB" instead of arithmetic you do in your head
            d = last.signal - self.tare_value
            head.append("delta ", style=FAINT)
            head.append(f"{d:+6.1f} dB", style="bold green" if d >= 0 else "bold red")
            head.append(f"   from tare at {self.tare_value:.1f} dBm", style=FAINT)
            head.append("   t to clear", style=FAINT)
        elif last:
            head.append("signal ", style=FAINT)
            head.append(f"{last.signal:6.1f} dBm", style="bold green")
            if last.snr is not None:
                window_vals = h.values(max(self.window, 60))
                if window_vals:
                    head.append(f"   {last.signal - min(window_vals):+.1f} vs window low",
                                style=FAINT)
            head.append(f"   {self.collector.iface or '?'}", style=FAINT)
            best_ever = h.best()
            if best_ever and last.signal > best_ever.signal:
                head.append("   NEW BEST", style="bold magenta")
        else:
            head.append("no connection", style="bold red")
            if s["error"]:
                head.append(f"   {s['error']}", style=FAINT)
        self.query_one("#headline", Static).update(head)

        now = s["now"]
        sub = Text()
        if now.count:
            sub.append(f"10s {now.mean:6.1f}   spread {now.jitter:4.1f}",
                       style="")
        tr = s["trend"]
        if tr is not None:
            sub.append(f"   trend {tr:+5.1f} dB/min", style=FAINT)
        sub.append(f"   stable {s['stability'] * 100:3.0f}%", style=FAINT)
        if last and last.chains:
            sub.append("   chains " + " ".join(f"{c:.0f}" for c in last.chains),
                       style=FAINT)
        if self.recorder:
            sub.append(f"   ● REC {time.time() - self.recording_started:4.0f}s",
                       style="bold red")
        self.query_one("#headline-sub", Static).update(sub)

        # single-row hint trace under the big number
        vals = h.values(self.window)
        self.query_one("#headline-spark", Static).update(
            plot(vals, width=min(100, self.size.width - 6), height=3,
                 live_style="green", axis=False)
            if vals else Text("collecting…", style=FAINT)
        )

        # main chart, optionally overlaid with the best run's trace
        chart_w = max(30, self.size.width - 42)
        chart_h = max(6, self.size.height - 22)
        zoom_lo = zoom_hi = None
        if self.headroom:
            from .chart import auto_range

            base_lo, base_hi = auto_range(self.collector.history.values(self.window)
                                          or [-70])
            zoom_lo, zoom_hi = base_lo + self.headroom, base_hi - self.headroom
            if zoom_lo >= zoom_hi:
                zoom_lo = zoom_hi = None
        if self.overlay and self.baseline and self.baseline.points:
            # Both traces get the same amount of time so "my first 30 seconds
            # now" lines up against "my first 30 seconds then". Absolute-time
            # placement would squash a short run into a sliver of the axis.
            span = max(10.0, self.baseline.span)
            live_cut = h.values(span)
            pts = [(t, v) for t, v in self.baseline.points if t <= span]
            xs = [t / span for t, _ in pts]
            chart = plot(live_cut, width=chart_w, height=chart_h,
                         lo=zoom_lo, hi=zoom_hi, smooth=self.smooth,
                         overlay=[v for _, v in pts], overlay_xs=xs)
            chart.append("\n", style=FAINT)
            chart.append(f"aligned view · last {span:.0f}s now vs "
                         f"{self.baseline.span:.0f}s run ({self.baseline.label})",
                         style=FAINT)
        else:
            chart = plot(vals, width=chart_w, height=chart_h,
                         lo=zoom_lo, hi=zoom_hi, smooth=self.smooth)
        self.query_one("#chart", Static).update(chart)

        self.query_one("#stats", Static).update(self.stats_panel(s))
        self.query_one("#right-panel", Static).update(self.right_panel(s))

    def stats_panel(self, s: dict) -> Text:
        t = Text()
        t.append(f"{'window':<9}{'avg':>7}{'med':>7}{'spread':>8}{'p10':>7}{'p90':>7}\n",
                 style=FAINT)
        for label, st in (("10 s", s["now"]), ("1 min", s["min1"]),
                          ("5 min", s["min5"]), ("session", s["session"])):
            if not st.count:
                t.append(f"{label:<9}      --\n", style=FAINT)
                continue
            t.append(f"{label:<9}{st.mean:7.1f}{st.median:7.1f}{st.jitter:8.1f}"
                     f"{st.p10:7.1f}{st.p90:7.1f}\n")
        lat = s["latency"]
        if lat:
            t.append("\nlatency  ")
            if lat["avg"] is not None:
                t.append(f"{lat['avg']:5.1f} ms", style="bold")
                t.append(f"   jitter {lat['jitter']:.1f}   loss {lat['loss']:.0f}%"
                         f"   → {lat['host']}", style=FAINT)
            else:
                t.append("no reply", style="bold red")
        t.append(f"\nlink coverage {s['coverage'] * 100:3.0f}%", style=FAINT)
        if s["reconnects"]:
            t.append(f"   drops {s['reconnects']}", style=FAINT)
        return t

    def right_panel(self, s: dict) -> Text:
        t = Text()
        design = self.lab.load(self.design_slug) if self.design_slug else None
        t.append("revision\n", style="bold")
        if design:
            t.append(f"{design.name}\n", style="bold cyan")
        else:
            t.append("none selected\n", style=FAINT)
            t.append("d pick   a new\n", style=FAINT)

        t.append("\nbest this session\n", style="bold")
        best = self.collector.history.best()
        if best:
            t.append(f"{best.signal:5.1f} dBm", style="")
            age = time.time() - best.ts
            t.append(("   just now\n" if age < 90 else f"   {age / 60:.0f}m ago\n"),
                     style=FAINT)
            now = s["now"]
            if now.count:
                gap = now.mean - best.signal
                a, style = arrow(gap)
                t.append(f"{a} current is {abs(gap):.1f} below peak\n", style=style)

        t.append("\nvs best run\n", style="bold")
        if self.baseline:
            if s["now"].count and self.baseline.stats.count:
                d = s["now"].mean - self.baseline.stats.mean
                a, style = arrow(d)
                t.append(f"{a} {d:+.1f} dB\n", style=style)
                t.append(f"{self.baseline.label} · median "
                         f"{self.baseline.stats.mean:.1f}\n", style=FAINT)
            else:
                t.append("waiting for samples\n", style=FAINT)
        else:
            t.append("no saved runs yet\n", style=FAINT)
            t.append("record with r\n", style=FAINT)

        if self.recorder:
            snap = self.recorder.snapshot()
            st = snap.stats()
            t.append("\nrecording\n", style="bold red")
            t.append(f"{snap.span:.0f}s  {len(snap.series)} buckets\n", style=FAINT)
            if st.count:
                t.append(f"avg {st.mean:.1f}  jitter {st.stdev:.1f}\n", style=FAINT)

        if self._probe and self._probe.running:
            t.append("\nthroughput\n", style="bold")
            t.append(f"running… {self._probe.live_mbps():5.1f} Mb/s\n")
        elif self.speed:
            t.append("\nthroughput\n", style="bold")
            if self.speed.ok:
                t.append(f"{self.speed.mbps:5.1f} Mb/s", style="bold green")
                t.append(f"\nttfb {self.speed.ttfb * 1000:.0f} ms  "
                         f"{self.speed.elapsed:.1f}s\n", style=FAINT)
            else:
                t.append(f"failed: {self.speed.error}\n", style="red")

        last = self.collector.history.last
        if last and (last.rx or last.tx):
            t.append("\nphy rate (negotiated)\n", style="bold")
            if last.tx:
                t.append(f"tx {last.tx:5.0f} Mb/s   ", style=FAINT)
            if last.rx:
                t.append(f"rx {last.rx:5.0f} Mb/s", style=FAINT)
            t.append("\n", style=FAINT)

        t.append("\nkeys\n", style="bold")
        for k, v in (("r", "record / stop"), ("s", "runs + compare"),
                     ("x", "speed test"), ("f", "focus mode"),
                     ("t", "tare to now"), ("m", "smoothing on/off"),
                     ("n", "auto-record"), ("⇧↑⇩", "chart range")):
            t.append(f"  {k:<3} {v}\n", style=FAINT)
        state = []
        if self.tare_value is not None:
            state.append(f"tare {self.tare_value:.1f}")
        state.append(f"smooth {'off' if not self.smooth else str(self.smooth)}")
        if self.autorecord:
            state.append("AUTO-REC")
        t.append("  " + "  ".join(state) + "\n", style=FAINT if not self.autorecord
                 else "bold red")
        return t

    # -- baseline --------------------------------------------------------
    def refresh_baseline(self) -> None:
        if not self.design_slug:
            self.baseline = None
            return
        best = None
        for sess in self.lab.sessions(self.design_slug):
            st = sess.stats()
            if not st.count:
                continue
            if best is None or st.median > best[1].median:
                best = (sess, st)
        if best:
            sess, st = best
            when = time.strftime("%d %b %H:%M", time.localtime(sess.started))
            self.baseline = Baseline(
                label=when,
                design=sess.design,
                stats=st,
                points=[(t, v) for t, v, _ in sess.series],
                span=sess.span,
            )
        else:
            self.baseline = None

    # -- actions ---------------------------------------------------------
    def action_focus(self) -> None:
        self.push_screen(FocusScreen(self.collector, self.baseline,
                                     self.tare_value))

    def action_log(self) -> None:
        h = self.collector.history
        s = self.collector.summary()
        best = h.best()
        parts = []
        if best:
            parts.append(f"best {best.signal:.1f} dBm at "
                         f"{time.strftime('%H:%M:%S', time.localtime(best.ts))}")
        st = s["session"]
        if st.count:
            parts.append(f"median {st.median:.1f} over {st.count} samples")
        odd = h.anomalies(3600)
        if odd:
            parts.append(f"{len(odd)} outliers in the last hour")
        self.notify(" · ".join(parts) if parts else "nothing notable yet", timeout=6)

    def action_ping(self) -> None:
        import threading

        def probe() -> None:
            from . import radio

            host = radio.gateway() or "1.1.1.1"
            self.collector.latency = radio.ping(host)
            self.paint()

        threading.Thread(target=probe, daemon=True).start()
        self.notify("pinging now", timeout=2)

    def action_threshold(self, amount: str) -> None:
        """Tighten or widen the y window by hand. The trace auto-fits, but on a
        very quiet link a manual zoom makes a 1 dB move obvious."""
        delta = int(amount.strip("()"))
        self.headroom = max(-30, min(30, self.headroom + delta))
        self.paint()

    def action_tare(self) -> None:
        """Zero the reading to the current signal, so the next element trim
        reads as a delta. Second press clears it."""
        if self.tare_value is not None:
            self.tare_value = None
            self.tare_at = None
            self.notify("tare cleared")
        else:
            last = self.collector.history.last
            if last is None:
                self.notify("no sample to tare against yet", severity="warning")
                return
            self.tare_value = last.signal
            self.tare_at = time.time()
            self.notify(f"tared at {last.signal:.1f} dBm — deltas from here")
        self.paint()

    def action_smooth(self) -> None:
        self.smooth = 0 if self.smooth else 10
        self.notify(f"smoothing {'off' if not self.smooth else f'{self.smooth}s'}")
        self.paint()

    def action_autorecord(self) -> None:
        """Keep a run going without remembering to press r. Saves the previous
        run and starts a fresh one each time it rolls over."""
        self.autorecord = not self.autorecord
        if self.autorecord:
            if not self.design_slug:
                self.autorecord = False
                self.notify("pick a revision first (d)", severity="warning")
                return
            if not self.recorder:
                self.recorder = labmod.Recorder(self.design_slug)
                self.recording_started = time.time()
            self.notify("auto-record on — press n again to stop")
        else:
            self.notify("auto-record off (current run still open; r saves it)")
        self.paint()

    def _autorecord_tick(self) -> None:
        """Split the run every 10 min so a long session stays comparable."""
        if not (self.autorecord and self.recorder):
            return
        if self.recorder.elapsed < 600:
            return
        meta = self.recorder.stop("auto-record segment")
        if meta.series:
            self.lab.save_session(meta)
        self.recorder = labmod.Recorder(self.design_slug)
        self.recording_started = time.time()
        self.notify(f"auto-saved {meta.span:.0f}s segment")

    def action_overlay(self) -> None:
        if not self.baseline:
            self.notify("no saved run to overlay - record one first",
                        severity="warning")
            return
        self.overlay = not self.overlay
        self.notify(f"best-run overlay {'on' if self.overlay else 'off'}")
        self.paint()

    def action_record(self) -> None:
        if self.recorder:
            meta = self.recorder.stop()
            if meta.series:
                self.lab.save_session(meta)
                st = meta.stats()
                self.notify(f"saved {meta.span:.0f}s · median {st.median:.1f} dBm")
            else:
                self.notify("nothing recorded", severity="warning")
            self.recorder = None
            self.refresh_baseline()
        else:
            if not self.design_slug:
                self.notify("pick a revision first (d) or make one (a)",
                            severity="warning")
                return
            self.recorder = labmod.Recorder(self.design_slug)
            self.recording_started = time.time()
            self.notify("recording — press r again to save")
        self.paint()

    def action_speed(self) -> None:
        if self._probe and self._probe.running:
            self.notify("probe already running", timeout=2)
            return
        self._probe = speedtest.ThroughputProbe().start()
        self.notify("throughput probe started")
        self.set_timer(0.5, self._poll_speed)

    def _poll_speed(self) -> None:
        if self._probe is None:
            return
        if self._probe.running:
            self.set_timer(0.5, self._poll_speed)
            self.paint()
            return
        self.speed = self._probe.result
        if self.speed.ok:
            self.notify(f"{self.speed.mbps:.1f} Mb/s", timeout=4)
        else:
            self.notify(f"probe failed: {self.speed.error}", severity="error")
        self.paint()

    def action_add_design(self) -> None:
        def done(result: tuple[str, str] | None) -> None:
            if not result:
                return
            name, desc = result
            d = self.lab.create(name, desc)
            self.design_slug = d.slug
            self.sub_title = d.name
            self.notify(f"created {d.slug}")
            self.refresh_baseline()
            self.paint()

        self.push_screen(NewDesignScreen(), done)

    def action_designs(self) -> None:
        def done(slug: str | None) -> None:
            if slug:
                self.design_slug = slug
                d = self.lab.load(slug)
                self.sub_title = d.name if d else ""
                self.refresh_baseline()
                self.paint()

        self.push_screen(PickDesignScreen(self.lab, self.design_slug), done)

    def action_sessions(self) -> None:
        if not self.design_slug:
            self.notify("pick a revision first (d)", severity="warning")
            return
        self.push_screen(SessionsScreen(self))

    def action_compare(self) -> None:
        if not self.design_slug:
            self.notify("pick a revision first (d)", severity="warning")
            return
        sessions = self.lab.sessions(self.design_slug)
        if len(sessions) < 2:
            self.notify("need at least two runs to compare", severity="warning")
            return
        self.push_screen(CompareScreen(self.lab, sessions[-2], sessions[-1]))

    def action_quit(self) -> None:  # type: ignore[override]
        if self.recorder:
            meta = self.recorder.stop()
            if meta.series:
                self.lab.save_session(meta)
        self.collector.stop()
        self.exit()


def run(iface: str | None = None, demo: bool = False, interval: float = 1.0,
        ping_host: str | None = None, design: str | None = None) -> None:
    WifiAnt(iface=iface, demo=demo, interval=interval, ping_host=ping_host,
            design=design).run()

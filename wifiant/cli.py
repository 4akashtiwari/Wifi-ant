"""Command line front door.

    wifiant                      live TUI
    wifiant --demo               TUI against a simulated radio
    wifiant --iface wlp2s0       watch a specific interface
    wifiant record NAME          headless recording (cron/ssh/ssh+touch)
    wifiant designs              list revisions
    wifiant new NAME             add a revision
    wifiant sessions NAME        list runs for a revision
    wifiant compare A B          side by side verdict
    wifiant stats                one-shot dump of the current link
    wifiant speed                throughput probe
    wifiant sweep                quick scan of nearby APs (antenna direction)
"""

from __future__ import annotations

import argparse
import pathlib
import signal
import sys
import time

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__, designs, radio, speedtest, watch
from .chart import hourly_bars, plot
from .stats import Sample

console = Console()


# -- helpers -------------------------------------------------------------
def _fmt(v, spec=".1f", dash="--"):
    return dash if v is None else format(v, spec)


def _yes_no(v: bool) -> str:
    return "yes" if v else "no"


# -- subcommands ---------------------------------------------------------
def cmd_stats(args) -> int:
    iface = radio.pick_iface(args.iface)
    if not iface:
        console.print("[red]no wireless interface found[/]")
        return 2
    s = radio.read_sample(iface)
    if not s.connected:
        console.print(f"[red]{iface} is not connected[/]")
        return 2

    table = Table(title=f"{iface}  {s.ssid or ''}", show_header=False, box=None)
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("bssid", s.bssid or "--")
    table.add_row("freq", f"{s.freq_mhz} MHz")
    table.add_row("signal", f"[bold]{_fmt(s.signal_dbm)} dBm[/]")
    table.add_row("noise", f"{_fmt(s.noise_dbm)} dBm")
    table.add_row("snr", f"{_fmt(s.snr_db)} dB")
    if s.chains:
        table.add_row("chains", "  ".join(f"{c:.0f}" for c in s.chains))
    table.add_row("phy rx", f"{_fmt(s.rx_bitrate, '.0f')} Mb/s")
    table.add_row("phy tx", f"{_fmt(s.tx_bitrate, '.0f')} Mb/s")
    if s.source:
        table.add_row("source", s.source)
    console.print(table)

    gw = radio.gateway()
    if gw and not args.no_ping:
        p = radio.ping(gw)
        if p and p["avg"] is not None:
            console.print(
                f"[dim]gateway {gw}:[/] {p['avg']:.1f} ms avg, "
                f"jitter {p['jitter']:.1f}, loss {p['loss']:.0f}%"
            )
    return 0


def cmd_history(args) -> int:
    """Print the last N samples as an ASCII trace - handy over ssh."""
    iface = radio.pick_iface(args.iface)
    if not iface:
        console.print("[red]no wireless interface found[/]")
        return 2
    console.print(f"[dim]sampling {iface} for {args.seconds}s...[/]")
    t0 = time.time()
    vals: list[float] = []
    while time.time() - t0 < args.seconds:
        s = radio.read_sample(iface)
        if s.connected and s.signal_dbm is not None:
            vals.append(s.signal_dbm)
        time.sleep(1.0)
    if not vals:
        console.print("[red]no samples[/]")
        return 2
    console.print(plot(vals, width=min(70, console.width - 8), height=8))
    console.print(
        f"[dim]min {min(vals):.1f}  avg {sum(vals) / len(vals):.1f}  "
        f"max {max(vals):.1f}  n={len(vals)}[/]"
    )
    return 0


def cmd_designs(args) -> int:
    lab = designs.Lab()
    rows = lab.designs()
    if not rows:
        console.print("[dim]no designs yet - wifiant new NAME[/]")
        return 0
    table = Table(title="antenna revisions")
    for col in ("slug", "name", "runs", "best avg", "last run"):
        table.add_column(col)
    for d in rows:
        sessions = lab.sessions(d.slug)
        best = None
        for s in sessions:
            st = s.stats()
            if st.count and (best is None or st.mean > best):
                best = st.mean
        last = sessions[-1].started if sessions else None
        table.add_row(
            d.slug,
            d.name,
            str(len(sessions)),
            _fmt(best),
            time.strftime("%d %b %H:%M", time.localtime(last)) if last else "--",
        )
    console.print(table)
    return 0


def cmd_new(args) -> int:
    lab = designs.Lab()
    d = lab.create(args.name, args.description or "")
    console.print(f"[green]created[/] {d.slug}  at {lab.path(d.slug)}")
    return 0


def cmd_sessions(args) -> int:
    lab = designs.Lab()
    design = args.design
    if not design:
        all_designs = lab.designs()
        if len(all_designs) == 1:
            design = all_designs[0].slug
        else:
            console.print("[red]which design? pass a slug[/]")
            return 2
    sessions = lab.sessions(design)
    if not sessions:
        console.print("[dim]no sessions[/]")
        return 0
    table = Table(title=f"{design} sessions")
    for col in ("session", "when", "length", "median", "jitter", "stable"):
        table.add_column(col)
    for s in sessions:
        st = s.stats()
        table.add_row(
            s.sid,
            time.strftime("%d %b %H:%M", time.localtime(s.started)),
            f"{s.span:.0f}s",
            _fmt(st.median),
            _fmt(st.stdev),
            f"{s.stability() * 100:.0f}%" if s.stability() is not None else "--",
        )
    console.print(table)
    return 0


def cmd_record(args) -> int:
    lab = designs.Lab()
    if lab.load(args.design) is None:
        console.print(f"[red]no such design: {args.design}[/]")
        return 2
    iface = radio.pick_iface(args.iface)
    if not iface and not args.demo:
        console.print("[red]no wireless interface[/]")
        return 2
    from . import mock

    fake = mock.MockRadio() if args.demo else None
    rec = designs.Recorder(args.design)
    stop_at = time.time() + args.seconds if args.seconds else 0
    console.print(f"[dim]recording {args.design} for "
                  f"{args.seconds or 'until ^C'}s (Ctrl-C to stop)[/]")
    interrupted = False

    def _stop(signum, frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _stop)
    try:
        while True:
            if stop_at and time.time() >= stop_at:
                break
            if interrupted:
                break
            s = fake.read() if fake else radio.read_sample(iface)
            if s.connected and s.signal_dbm is not None:
                rec.feed(Sample(ts=s.ts, signal=s.signal_dbm, noise=s.noise_dbm,
                                snr=s.snr_db, chains=tuple(s.chains)))
                if args.verbose:
                    console.print(f"  {s.signal_dbm:6.1f} dBm")
            time.sleep(args.interval)
    finally:
        meta = rec.stop()
        if meta.series:
            path = lab.save_session(meta)
            st = meta.stats()
            console.print(f"[green]saved[/] {path.name}  "
                          f"{meta.span:.0f}s  median {st.median:.1f} dBm  "
                          f"jitter {st.stdev:.1f}")
        else:
            console.print("[yellow]nothing recorded[/]")
    return 0


def _load_named(lab: designs.Lab, ref: str) -> designs.SessionMeta | None:
    """Accept a session id, a session id prefix, or '' for the latest of a
    design. Users type fragments, not uuids."""
    candidates = lab.sessions()
    for s in candidates:
        if s.sid == ref:
            return s
    matches = [s for s in candidates if s.sid.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


def cmd_compare(args) -> int:
    lab = designs.Lab()
    a = _load_named(lab, args.a)
    b = _load_named(lab, args.b)
    if a is None:
        console.print(f"[red]can't find session {args.a}[/]")
        return 2
    if b is None:
        console.print(f"[red]can't find session {args.b}[/]")
        return 2

    cmp = lab.compare(a, b, args.window)
    ta = Table(title=f"A = {a.design} / {a.sid}", show_header=False)
    ta.add_column(style="dim")
    ta.add_column()
    ta.add_row("window", f"{cmp.window:.0f}s")
    ta.add_row("mean", f"{_fmt(cmp.a_stats.mean)} dBm")
    ta.add_row("median", f"{_fmt(cmp.a_stats.median)} dBm")
    ta.add_row("jitter", f"{_fmt(cmp.a_stats.stdev)}")
    ta.add_row("stable", f"{cmp.a_stability * 100:.0f}%"
               if cmp.a_stability is not None else "--")

    tb = Table(title=f"B = {b.design} / {b.sid}", show_header=False)
    tb.add_column(style="dim")
    tb.add_column()
    tb.add_row("window", f"{cmp.window:.0f}s")
    tb.add_row("mean", f"{_fmt(cmp.b_stats.mean)} dBm")
    tb.add_row("median", f"{_fmt(cmp.b_stats.median)} dBm")
    tb.add_row("jitter", f"{_fmt(cmp.b_stats.stdev)}")
    tb.add_row("stable", f"{cmp.b_stability * 100:.0f}%"
               if cmp.b_stability is not None else "--")

    from rich.columns import Columns

    console.print(Columns([ta, tb]))
    console.print(f"[bold]{cmp.verdict()}[/]")

    # overlay both traces on one axis
    a_vals = a.values(cmp.window)
    b_vals = b.values(cmp.window)
    if a_vals and b_vals:
        xs_b = [p[0] / cmp.window for p in b.series if p[0] <= cmp.window]
        console.print(plot(b_vals, width=min(70, console.width - 10), height=8,
                           overlay=a_vals, overlay_xs=xs_b or None,
                           live_style="green", overlay_style="blue"))
        console.print("[dim]green = A   blue = B   yellow = overlap[/]")
    return 0


def cmd_speed(args) -> int:
    console.print("[dim]probing throughput...[/]")
    res = speedtest.quick_probe(args.url, size=args.size)
    if not res.ok:
        console.print(f"[red]failed: {res.error}[/]")
        return 2
    console.print(f"[bold]{res.mbps:.1f} Mb/s[/]  "
                  f"({res.bytes / 1e6:.1f} MB in {res.elapsed:.1f}s, "
                  f"ttfb {res.ttfb * 1000:.0f} ms)")
    return 0


def cmd_config(args) -> int:
    """Print the resolved config as JSON - handy for scripting/tmux status."""
    import json

    iface = radio.pick_iface(args.iface)
    s = radio.read_sample(iface) if iface else radio.RadioSample()
    lat = radio.ping(args.ping or radio.gateway() or "1.1.1.1") if args.ping_always else None
    console.print_json(json.dumps({
        "iface": iface,
        "ssid": s.ssid,
        "bssid": s.bssid,
        "signal": s.signal_dbm,
        "noise": s.noise_dbm,
        "snr": s.snr_db,
        "chains": s.chains,
        "rx": s.rx_bitrate,
        "tx": s.tx_bitrate,
        "latency": lat,
        "home": str(designs.data_home()),
    }))
    return 0


def cmd_sweep(args) -> int:
    """List what the radio can see. A directional antenna shows its pattern
    here before you ever connect."""
    iface = radio.pick_iface(args.iface)
    if not iface:
        console.print("[red]no wireless interface[/]")
        return 2
    from . import scan

    rows = scan.nearby(iface, rescan=args.rescan)
    if not rows:
        console.print("[dim]no scan data - try --rescan[/]")
        return 0
    rows.sort(key=lambda r: r.signal, reverse=True)
    table = Table(title=f"nearby APs ({iface})")
    for col in ("signal", "ssid", "bssid", "freq", "bars"):
        table.add_column(col, justify="right" if col != "ssid" else "left")
    best = rows[0].signal
    for r in rows:
        rel = r.signal - best
        table.add_row(
            f"{r.signal:.0f}",
            r.ssid or "[dim](hidden)[/]",
            r.bssid,
            str(r.freq),
            f"[green]{'█' * max(0, min(10, 5 + int((r.signal + 90) / 8))):10}[/]"
            f" [dim]{rel:+.0f}[/]",
        )
    console.print(table)
    return 0


def cmd_watch(args) -> int:
    """Long-run logging. One line per interval, one file per day."""
    iface = radio.pick_iface(args.iface)
    if not iface and not args.demo:
        console.print("[red]no wireless interface[/]")
        return 2
    if args.design and designs.Lab().load(args.design) is None:
        console.print(f"[red]no such design: {args.design}[/]")
        return 2

    from . import mock
    from .collector import Collector

    collector = Collector(iface=iface, demo=args.demo,
                          interval=min(2.0, args.interval),
                          ping_host=args.ping_host).start()
    flag = {"stop": False}
    watch.install_signal_handlers(flag)

    console.print(f"[dim]watching {iface or 'demo'} every {args.interval:.0f}s"
                  + (f" for {args.duration:.0f}s" if args.duration else "")
                  + " — ^C to stop[/]")
    started_rows = 0

    def tick(row: dict) -> None:
        nonlocal started_rows
        started_rows += 1
        if args.verbose:
            console.print(f"  {time.strftime('%H:%M:%S', time.localtime(row['t']))}"
                          f"  {row['mean']:6.1f} dBm  ({row['lo']:.0f}..{row['hi']:.0f})")

    class _StopAware:
        """Tiny shim so run_watch can see the signal flag without knowing
        about the collector."""

        def __init__(self, coll, flag):
            self.history = coll.history
            self.iface = coll.iface
            self._flag = flag

        @property
        def stopped(self) -> bool:
            return self._flag["stop"]

    log = watch.run_watch(_StopAware(collector, flag), args.design,
                          args.interval, args.duration, on_tick=tick)
    collector.stop()

    if not log.rows:
        console.print("[yellow]nothing logged[/]")
        return 0
    rep = watch.analyse(log.rows)
    console.print(f"[green]{started_rows} rows written[/]  "
                  f"{watch.watch_dir()}")
    console.print(f"covered {rep.total:.1f} h   spread {rep.spread:.1f} dB")
    if rep.best_hour is not None:
        console.print(f"best hour {rep.best_hour:02d}:00 "
                      f"({rep.hours[rep.best_hour]:.1f} dBm)   "
                      f"worst {rep.worst_hour:02d}:00 "
                      f"({rep.hours[rep.worst_hour]:.1f} dBm)")
    return 0


def cmd_analyze(args) -> int:
    files = watch.DayLog.files()
    if args.file:
        files = [p for p in files if args.file in p.name]
    if not files:
        console.print("[dim]no watch logs yet - wifiant watch --design NAME[/]")
        return 0
    rows: list[dict] = []
    for f in files:
        rows.extend(watch.DayLog.load(f))
    if not rows:
        console.print("[red]logs are empty[/]")
        return 0

    rep = watch.analyse(rows, args.dip_threshold)
    console.print(f"[bold]{len(rows)} samples[/] over {rep.total:.1f} h "
                  f"from {len(files)} file(s)")
    console.print(f"spread {rep.spread:.1f} dB   "
                  f"best hour {rep.best_hour:02d}:00   "
                  f"worst hour {rep.worst_hour:02d}:00")
    if rep.best_window and rep.total >= 1.0:
        console.print(f"best one-hour window "
                      f"{time.strftime('%d %b %H:%M', time.localtime(rep.best_window[0]))}"
                      f" – {time.strftime('%H:%M', time.localtime(rep.best_window[1]))}")

    console.print()
    console.print(Text("mean signal by hour", style="bold"))
    console.print(hourly_bars(rep.hours))

    if rep.noise_by_hour:
        console.print()
        console.print(Text("noise floor by hour", style="bold"))
        console.print(hourly_bars(rep.noise_by_hour))

    if rep.dips:
        console.print()
        table = Table(title="dips (runs at least "
                            f"{args.dip_threshold:.0f} dB below median)")
        table.add_column("start")
        table.add_column("length")
        table.add_column("low")
        for d in rep.dips[:15]:
            table.add_row(
                time.strftime("%d %b %H:%M", time.localtime(d["start"])),
                f"{d['end'] - d['start']:.0f}s",
                f"{d['low']:.1f}",
            )
        console.print(table)
    return 0


def cmd_export(args) -> int:
    lab = designs.Lab()
    out = pathlib.Path(args.output).expanduser() if args.output else None
    path, rows = lab.export_csv(out, args.design)
    if rows:
        console.print(f"[green]{rows} rows[/] → {path}")
    else:
        console.print("[yellow]no recorded sessions yet[/] - run a recording "
                      "first (wifiant record NAME, or r in the TUI)")
    return 0


def cmd_tui(args) -> int:
    from .app import run

    run(iface=args.iface, demo=args.demo, interval=args.interval,
        ping_host=args.ping)
    return 0


# -- entry point ---------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wifiant",
        description="live antenna lab for your Wi-Fi link",
    )
    p.add_argument("--version", action="version", version=f"wifiant {__version__}")
    p.add_argument("--demo", action="store_true", help="use a simulated radio")
    sub = p.add_subparsers(dest="cmd")

    t = sub.add_parser("tui", help="launch the live view (default)")
    t.add_argument("--iface")
    t.add_argument("--demo", action="store_true")
    t.add_argument("--interval", type=float, default=1.0)
    t.add_argument("--ping", help="host to ping for latency")
    t.set_defaults(func=cmd_tui)

    s = sub.add_parser("stats", help="one-shot link dump")
    s.add_argument("--iface")
    s.add_argument("--no-ping", action="store_true")
    s.set_defaults(func=cmd_stats)

    h = sub.add_parser("history", help="sample for N seconds and plot")
    h.add_argument("--iface")
    h.add_argument("--seconds", type=float, default=30)
    h.set_defaults(func=cmd_history)

    d = sub.add_parser("designs", help="list revisions")
    d.set_defaults(func=cmd_designs)

    n = sub.add_parser("new", help="create a revision")
    n.add_argument("name")
    n.add_argument("--description", "-d", default="")
    n.set_defaults(func=cmd_new)

    ss = sub.add_parser("sessions", help="list runs")
    ss.add_argument("design", nargs="?")
    ss.set_defaults(func=cmd_sessions)

    r = sub.add_parser("record", help="record a run headlessly")
    r.add_argument("design")
    r.add_argument("--seconds", type=float, default=0)
    r.add_argument("--interval", type=float, default=1.0)
    r.add_argument("--iface")
    r.add_argument("--demo", action="store_true")
    r.add_argument("--verbose", "-v", action="store_true")
    r.set_defaults(func=cmd_record)

    c = sub.add_parser("compare", help="compare two sessions")
    c.add_argument("a", help="session id (prefix ok) or exact id")
    c.add_argument("b")
    c.add_argument("--window", type=float, default=None,
                   help="seconds to compare from the start of each run")
    c.set_defaults(func=cmd_compare)

    sp = sub.add_parser("speed", help="throughput probe")
    sp.add_argument("--url", default=None)
    sp.add_argument("--size", type=int, default=5_000_000)
    sp.set_defaults(func=cmd_speed)

    w = sub.add_parser("watch", help="log the link for hours into a day file")
    w.add_argument("--design", help="tag the log with a revision")
    w.add_argument("--interval", type=float, default=60.0,
                   help="seconds between logged rows (default 60)")
    w.add_argument("--duration", type=float, default=0,
                   help="stop after N seconds (default: until ^C)")
    w.add_argument("--iface")
    w.add_argument("--demo", action="store_true")
    w.add_argument("--ping-host")
    w.add_argument("--verbose", "-v", action="store_true")
    w.set_defaults(func=cmd_watch)

    an = sub.add_parser("analyze", help="report on watch logs")
    an.add_argument("--file", help="only files whose name contains this")
    an.add_argument("--dip-threshold", type=float, default=6.0)
    an.set_defaults(func=cmd_analyze)

    ex = sub.add_parser("export", help="dump sessions to CSV")
    ex.add_argument("--design", help="only this revision")
    ex.add_argument("--output", "-o")
    ex.set_defaults(func=cmd_export)

    cf = sub.add_parser("config", help="one-shot JSON dump for scripting")
    cf.add_argument("--iface")
    cf.add_argument("--ping")
    cf.add_argument("--ping-always", action="store_true")
    cf.set_defaults(func=cmd_config)

    sw = sub.add_parser("sweep", help="list nearby APs")
    sw.add_argument("--iface")
    sw.add_argument("--rescan", action="store_true")
    sw.set_defaults(func=cmd_sweep)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    # --version / --help have to work before we prepend a subcommand
    if argv and argv[0] in ("-h", "--help", "--version", "-V"):
        parser.parse_args(argv)
        return 0

    # bare `wifiant` (with maybe some flags) should start the TUI, not error
    if not argv or argv[0].startswith("-"):
        args = parser.parse_args(["tui", *argv])
    else:
        args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())

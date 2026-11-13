"""Braille plots for the terminal.

A single sparkline row hides exactly what you're looking for when tuning an
antenna: the shape. Braille cells carry 2x4 dots, so a 60x10 pane gives you
120x40 resolvable points - enough to see a fade dip without redrawing the
whole screen every frame.
"""

from __future__ import annotations

from collections import deque

from rich.text import Text

# braille dot -> bit, in the order the cells lay them out
_DOTS = [
    (0x01, 0, 0), (0x02, 0, 1), (0x04, 0, 2), (0x40, 0, 3),
    (0x08, 1, 0), (0x10, 1, 1), (0x20, 1, 2), (0x80, 1, 3),
]


def _paint(bits: list[list[int]], x: int, y: int) -> None:
    if 0 <= y < len(bits) and 0 <= x < len(bits[0]):
        bits[y][x] = 1


def _line(bits: list[list[int]], x0: int, y0: int, x1: int, y1: int) -> None:
    """Bresenham, so a fast-moving trace still reads as a continuous line."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _paint(bits, x0, y0)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _resample(series: list[float], points: int) -> list[float]:
    """Average whole buckets so a dense series uses braille's full 2x4 grid
    instead of leaving every other subcolumn empty."""
    if len(series) <= points or points <= 0:
        return series
    out = []
    n = len(series)
    for i in range(points):
        a = int(i * n / points)
        b = max(a + 1, int((i + 1) * n / points))
        chunk = series[a:b]
        out.append(sum(chunk) / len(chunk))
    return out


def moving_average(values: list[float], window: int) -> list[float]:
    """Trailing average. Trailing rather than centred on purpose: when you're
    holding an element and watching the number, you want the line to react to
    what just happened, not to smooth the past."""
    if window <= 1 or not values:
        return list(values)
    q: deque[float] = deque()
    total = 0.0
    out = []
    for v in values:
        q.append(v)
        total += v
        if len(q) > window:
            total -= q.popleft()
        out.append(total / len(q))
    return out


def _bits_for(series: list[float], width: int, height: int, lo: float, hi: float,
              xs: list[float] | None = None) -> list[list[int]]:
    w, h = width * 2, height * 4
    bits = [[0] * w for _ in range(h)]
    if not series or hi <= lo:
        return bits
    span = max(1e-9, hi - lo)
    if xs is None:
        # only decimate; never stretch a short series across the full width
        if len(series) > w * 2:
            series = _resample(series, w * 2)

    def col(i: int) -> int:
        if xs:
            x = int(xs[i] * (w - 1))
        elif len(series) == 1:
            x = w - 1
        else:
            x = int(i * (w - 1) / (len(series) - 1))
        return max(0, min(w - 1, x))

    prev = None
    for i, v in enumerate(series):
        y = int((hi - v) / span * (h - 1))
        y = max(0, min(h - 1, y))
        x = col(i)
        if prev is not None:
            _line(bits, prev[0], prev[1], x, y)
        else:
            _paint(bits, x, y)
        prev = (x, y)
    return bits


def _cell(bits: list[list[int]], cx: int, cy: int) -> int:
    """Assemble one braille character for character cell (cx, cy)."""
    out = 0
    for bit, dx, dy in _DOTS:
        y = cy * 4 + dy
        x = cx * 2 + dx
        if y < len(bits) and x < len(bits[0]) and bits[y][x]:
            out |= bit
    return out


def auto_range(values: list[float], minimum_span: float = 8.0) -> tuple[float, float]:
    """Pick a y window that shows detail without lying about the scale."""
    if not values:
        return -95.0, -25.0
    lo, hi = min(values), max(values)
    if hi - lo < minimum_span:
        mid = (hi + lo) / 2
        lo, hi = mid - minimum_span / 2, mid + minimum_span / 2
    return lo, hi


def plot(live: list[float], width: int = 60, height: int = 9,
         lo: float | None = None, hi: float | None = None,
         overlay: list[float] | None = None,
         overlay_xs: list[float] | None = None,
         smooth: int = 0,
         live_style: str = "green",
         overlay_style: str = "blue",
         smooth_style: str = "yellow",
         axis: bool = True) -> Text:
    """Render one series, optionally against another on the same axes.

    ``overlay_xs`` lets a recorded session share the live time axis (0..1),
    which is what makes "now vs best run" a fair visual comparison. Pass
    ``lo``/``hi`` to pin the range, otherwise it fits the data. ``smooth`` is
    a trailing moving-average window in samples, drawn over the raw trace.
    """
    everything = list(live) + list(overlay or [])
    auto_lo, auto_hi = auto_range(everything)
    lo = auto_lo if lo is None else lo
    hi = auto_hi if hi is None else hi
    # keep a little headroom so the trace never sits flat on the border
    pad = max(0.8, (hi - lo) * 0.08)
    lo -= pad
    hi += pad

    plot_w = width - (6 if axis else 0)
    live_bits = _bits_for(live, plot_w, height, lo, hi)
    over_bits = (
        _bits_for(overlay, plot_w, height, lo, hi, overlay_xs) if overlay else None
    )
    ma_bits = None
    if smooth > 1 and len(live) >= 3:
        ma_bits = _bits_for(moving_average(live, smooth), plot_w, height,
                            lo, hi)

    text = Text()
    for cy in range(height):
        for cx in range(plot_w):
            live_c = _cell(live_bits, cx, cy)
            over_c = _cell(over_bits, cx, cy) if over_bits else 0
            ma_c = _cell(ma_bits, cx, cy) if ma_bits else 0
            if ma_c and (live_c or over_c):
                text.append(chr(0x2800 | live_c | over_c | ma_c),
                            style="bold " + smooth_style)
            elif ma_c:
                text.append(chr(0x2800 | ma_c), style=smooth_style)
            elif live_c and over_c:
                text.append(chr(0x2800 | live_c | over_c), style="yellow")
            elif live_c:
                text.append(chr(0x2800 | live_c), style=live_style)
            elif over_c:
                text.append(chr(0x2800 | over_c), style=overlay_style)
            else:
                text.append(" ")
        if axis:
            if cy == 0:
                label = f"{hi:5.0f} "
            elif cy == height // 2:
                label = f"{(hi + lo) / 2:5.0f} "
            elif cy == height - 1:
                label = f"{lo:5.0f} "
            else:
                label = "      "
            text.append(label, style="dim")
        if cy != height - 1:
            text.append("\n")
    return text


def hourly_bars(by_hour: dict[int, float], width: int = 14,
                columns: int = 3) -> Text:
    """Bars for each hour of the day, laid out as a grid so it fits a terminal.
    Used by `wifiant analyze` to show the shape of a day at a glance."""
    text = Text()
    if not by_hour:
        return text
    vals = list(by_hour.values())
    lo, hi = auto_range(vals, minimum_span=4.0)
    span = max(1e-9, hi - lo)
    cell = width + 10

    def bar_for(hour: int) -> tuple[str, str]:
        v = by_hour.get(hour)
        if v is None:
            return f"{hour:02d} " + "·" * width + "     ", "dim"
        frac = (v - lo) / span
        filled = max(1, int(round(frac * width)))
        style = "green" if frac > 0.66 else "yellow" if frac > 0.33 else "red"
        return f"{hour:02d} " + "█" * filled, style

    for row_start in range(0, 24, columns):
        for hour in range(row_start, min(row_start + columns, 24)):
            label, style = bar_for(hour)
            used = len(label) + (7 if hour in by_hour else 0)
            text.append(label, style=style)
            if hour in by_hour:
                text.append(f"{by_hour[hour]:6.0f} ", style="dim")
            else:
                text.append(" " * max(0, cell - used), style="dim")
        text.append("\n")
    return text

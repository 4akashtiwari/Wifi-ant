"""Shared presentation helpers.

Everything here is about turning numbers into something readable at a glance -
colour bands, meters, compact labels. Kept separate from the widgets so the
rules stay in one place instead of drifting between screens.
"""

from __future__ import annotations

from rich.text import Text

# Signal bands, from "you're basically cabled" to "why bother".
BANDS = [
    (-50, "excellent", "bright_green"),
    (-60, "good", "green"),
    (-67, "fair", "yellow"),
    (-75, "weak", "dark_orange"),
    (-999, "poor", "red"),
]


def band(dbm: float | None) -> tuple[str, str]:
    """(label, rich style) for a dBm reading."""
    if dbm is None:
        return "no link", "grey50"
    for limit, label, style in BANDS:
        if dbm > limit:
            return label, style
    return "poor", "red"


def quality_style(value: float) -> str:
    if value >= 80:
        return "bright_green"
    if value >= 60:
        return "green"
    if value >= 40:
        return "yellow"
    return "red"


def meter(frac: float, width: int = 16, filled: str = "━",
          empty: str = "─") -> str:
    """A horizontal bar. Shaded blocks read better than ASCII in a TUI."""
    frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
    n = int(round(frac * width))
    return filled * n + empty * (width - n)


def gauge(dbm: float | None, width: int = 18, show_label: bool = True) -> Text:
    """Signal as a filled/sectioned gauge with band colouring."""
    label, style = band(dbm)
    t = Text()
    if dbm is None:
        t.append("─" * width, style="grey30")
        if show_label:
            t.append(f"  {label}", style="grey50")
        return t
    # -95..-25 dBm onto 0..1
    frac = (dbm + 95) / 70.0
    n = max(0, min(width, int(round(frac * width))))
    t.append("━" * n, style=style)
    t.append("─" * (width - n), style="grey30")
    if show_label:
        t.append(f"  {label}", style=style)
    return t


def spark(values: list[float], width: int = 24, lo: float = -95,
          hi: float = -30) -> Text:
    """Compact trend line for tight spaces."""
    glyphs = "▁▂▃▄▅▆▇█"
    t = Text()
    if not values:
        t.append(" " * width, style="grey30")
        return t
    span = max(1.0, hi - lo)
    for v in values[-width:]:
        idx = int((v - lo) / span * (len(glyphs) - 1))
        idx = max(0, min(len(glyphs) - 1, idx))
        _, style = band(v)
        t.append(glyphs[idx], style=style)
    return t


def delta_text(d: float, unit: str = "dB", decimals: int = 1,
               small: float = 0.5) -> Text:
    """A signed difference, coloured by direction, greyed out when it's below
    the noise floor we care about."""
    t = Text()
    if abs(d) < small:
        t.append(f"{d:+.{decimals}f} {unit}", style="grey50")
        return t
    style = "bright_green" if d > 0 else "red"
    arrow = "▲" if d > 0 else "▼"
    t.append(f"{arrow} {d:+.{decimals}f} {unit}", style=style)
    return t


def keycaps(*pairs: tuple[str, str]) -> Text:
    """Keys rendered as compact chips so the footer doesn't blend into text."""
    t = Text()
    for i, (key, what) in enumerate(pairs):
        if i:
            t.append("  ")
        t.append(f" {key} ", style="reverse dim")
        t.append(f" {what}", style="grey50")
    return t


def ago(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.0f}d ago"


def dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"

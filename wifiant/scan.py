"""Nearby access points, for pointing a directional antenna.

Uses ``iw dev <iface> scan`` which needs either root or an unprivileged
scan still running on the interface. If neither is available the cache from
the last automatic scan is used instead - the kernel keeps it around.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .radio import _run


@dataclass
class Nearby:
    bssid: str
    ssid: str = ""
    freq: int = 0
    signal: float = -100.0
    security: str = ""


def _parse(scan_out: str) -> list[Nearby]:
    rows: list[Nearby] = []
    current: Nearby | None = None
    for raw in scan_out.splitlines():
        line = raw.strip()
        if line.startswith("BSS "):
            if current:
                rows.append(current)
            current = Nearby(bssid=line.split()[1].split("(")[0])
            continue
        if current is None:
            continue
        if line.startswith("freq:"):
            m = re.search(r"\d+", line)
            if m:
                current.freq = int(m.group())
        elif line.startswith("signal:"):
            m = re.search(r"-?\d+(?:\.\d+)?", line)
            if m:
                current.signal = float(m.group())
        elif line.startswith("SSID:"):
            current.ssid = line.split(":", 1)[1].strip()
        elif "RSN:" in line and not current.security:
            current.security = "wpa2/3"
        elif "WPA:" in line and not current.security:
            current.security = "wpa"
    if current:
        rows.append(current)
    return rows


def nearby(iface: str, rescan: bool = False) -> list[Nearby]:
    cmd = ["iw", "dev", iface, "scan"]
    if not rescan:
        cmd.append("dump")
    out = _run(cmd, timeout=15 if rescan else 5)
    rows = _parse(out)
    if rows:
        return rows
    # retry the cached scan if the live one was refused
    return _parse(_run(["iw", "dev", iface, "scan", "dump"], timeout=5))

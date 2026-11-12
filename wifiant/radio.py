"""Collects whatever the kernel is willing to tell us about the live link.

Sources, in order of preference:

  * ``iw dev <iface> link`` / ``iw dev <iface> station dump`` - dBm, noise,
    bitrates, retries, per-chain signal. Only needs the ``iw`` binary, no root.
  * ``/proc/net/wireless`` - dBm + link quality. Always there on Linux.
  * ``nmcli`` - only used to find out which interface carries the default
    route when we can't guess.

Everything is read-only. Nothing here touches the radio config.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

PROC_WIRELESS = "/proc/net/wireless"
ROUTE_TABLE = "/proc/net/route"
OPERSTATE = "/sys/class/net/{}/operstate"

# a reading is marked stale if the sample we're looking at is older than this
SAMPLE_MAX_AGE = 2.5


@dataclass
class RadioSample:
    """One snapshot of the radio. All optical values are Instant dBm."""

    iface: str = ""
    ssid: str = ""
    bssid: str = ""
    freq_mhz: int = 0
    signal_dbm: float | None = None
    noise_dbm: float | None = None
    snr_db: float | None = None
    # per antenna chain (MIMO), instant dBm, strongest first
    chains: list[float] = field(default_factory=list)
    rx_bitrate: float | None = None  # Mb/s, negotiated PHY rate (not payload!)
    tx_bitrate: float | None = None
    tx_failed: int = 0
    tx_retries: int = 0
    tx_packets: int = 0
    beacon_loss: int = 0
    connected: bool = False
    ts: float = field(default_factory=time.time)
    source: str = ""

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.ts)

    @property
    def is_live(self) -> bool:
        return self.connected and self.age < SAMPLE_MAX_AGE


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _default_route_iface() -> str:
    """Interface that carries the default route, straight from the kernel."""
    with open(ROUTE_TABLE, "r", encoding="utf-8", errors="replace") as fh:
        next(fh, None)
        for line in fh:
            parts = line.split()
            if len(parts) < 8:
                continue
            # destination 0.0.0.0, flags with RTF_UP set
            if parts[1] == "00000000" and (int(parts[3], 16) & 0x1):
                return parts[0]
    return ""


def wireless_ifaces() -> list[str]:
    """Names of interfaces the kernel has registered as wireless."""
    names = []
    for line in _read(PROC_WIRELESS).splitlines()[2:]:
        name = line.split(":", 1)[0].strip()
        if name:
            names.append(name)
    return names


def pick_iface(explicit: str | None = None) -> str:
    """Best guess at the interface we should watch, or "" if there is none."""
    if explicit:
        return explicit
    wl = wireless_ifaces()
    if not wl:
        return ""
    route = _default_route_iface()
    if route in wl:
        return route
    for name in wl:
        if _read(OPERSTATE.format(name)).strip() == "up":
            return name
    return wl[0]


def _parse_kv(block: str) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if ":" in line:
            k, _, v = line.partition(":")
            kv.setdefault(k.strip(), v.strip())
    return kv


def _num(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def _drain_iw(iface: str) -> list[str]:
    """`iw` keeps printing station entries; collect them so beacons show up."""
    out = _run(["iw", "dev", iface, "station", "dump"])
    if not out:
        return []
    # Re-run with a timeout so a busy radio still yields what's already there.
    return out.split("\nStation ")


def _chains_from_blocks(blocks: list[str]) -> list[float]:
    # Two layouts show up in the wild. Both put the chain after a label.
    chains: list[float] = []
    for block in blocks:
        for m in re.finditer(
            r"chain\[\d+\][^\n]*?(-?\d+(?:\.\d+)?)\s*dBm", block, re.IGNORECASE
        ):
            chains.append(float(m.group(1)))
        for m in re.finditer(
            r"antenna\s+\d+:\s*(-?\d+(?:\.\d+)?)\s*dBm", block, re.IGNORECASE
        ):
            chains.append(float(m.group(1)))
    return sorted(chains, reverse=True)


def read_iw(iface: str) -> RadioSample | None:
    if not shutil.which("iw"):
        return None
    link = _run(["iw", "dev", iface, "link"])
    if not link or "Not connected" in link:
        return None

    sample = RadioSample(iface=iface, connected=True, source="iw")
    for line in link.splitlines():
        line = line.strip()
        if line.startswith("SSID:"):
            sample.ssid = line.split(":", 1)[1].strip()
        elif line.startswith("Connected to"):
            sample.bssid = line.split()[2]
        elif line.startswith("freq:"):
            sample.freq_mhz = int(_num(line) or 0)
        elif line.startswith("signal:"):
            sample.signal_dbm = _num(line)
        elif line.startswith("noise:"):
            sample.noise_dbm = _num(line)
        elif line.startswith("rx bitrate:"):
            sample.rx_bitrate = _num(line)

    if sample.signal_dbm is None:
        return None

    # station dump is the only place retries and tx bitrate live
    blocks = _drain_iw(iface)
    for block in blocks:
        lines = block.splitlines()
        kv = _parse_kv("\n".join(lines))
        if "tx bitrate" in kv and sample.tx_bitrate is None:
            sample.tx_bitrate = _num(kv["tx bitrate"])
        if "rx bitrate" in kv and sample.rx_bitrate is None:
            sample.rx_bitrate = _num(kv["rx bitrate"])
        for k, attr in (
            ("tx failed", "tx_failed"),
            ("tx retries", "tx_retries"),
            ("tx packets", "tx_packets"),
            ("beacon loss", "beacon_loss"),
        ):
            if k in kv and _num(kv[k]) is not None:
                setattr(sample, attr, int(_num(kv[k]) or 0))
        sample.chains = _chains_from_blocks([block])
        if sample.chains:
            break

    if sample.chains and sample.signal_dbm is None:
        sample.signal_dbm = sample.chains[0]
    if sample.signal_dbm is not None and sample.noise_dbm is not None:
        sample.snr_db = sample.signal_dbm - sample.noise_dbm
    return sample


def read_proc_wireless(iface: str) -> RadioSample | None:
    text = _read(PROC_WIRELESS)
    if not text:
        return None
    for line in text.splitlines()[2:]:
        name = line.split(":", 1)[0].strip()
        if name != iface:
            continue
        parts = line.replace(".", " ").split()
        # name, status, link quality, level, noise, ...
        level = _num(parts[3]) if len(parts) > 3 else None
        noise = _num(parts[4]) if len(parts) > 4 else None
        if level is None:
            return None
        sample = RadioSample(
            iface=iface,
            connected=True,
            signal_dbm=level,
            noise_dbm=noise,
            source="procfs",
        )
        if noise is not None:
            sample.snr_db = level - noise
        return sample
    return None


def read_nmcli_signal(iface: str) -> RadioSample | None:
    """Fallback for drivers that hide both iw and procfs numbers."""
    if not shutil.which("nmcli"):
        return None
    out = _run(
        [
            "nmcli",
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,FREQ,BSSID",
            "device",
            "wifi",
            "list",
            "ifname",
            iface,
        ]
    )
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 5 or parts[0].strip() != "*":
            continue
        signal = _num(parts[2])
        if signal is None:
            continue
        # nmcli reports 0-100; map onto the usual -30..-90 dBm scale
        dbm = (signal / 2) - 100 if signal <= 100 else signal
        return RadioSample(
            iface=iface,
            ssid=parts[1].strip(),
            bssid=parts[4].strip(),
            freq_mhz=int(_num(parts[3]) or 0),
            signal_dbm=dbm,
            connected=True,
            source="nmcli",
        )
    return None


def read_sample(iface: str) -> RadioSample:
    """Best available sample for this interface. Never raises."""
    for reader in (read_iw, read_proc_wireless, read_nmcli_signal):
        try:
            sample = reader(iface)
        except Exception:
            sample = None
        if sample is not None:
            return sample
    return RadioSample(iface=iface, connected=False)


def link_speed_mbps(iface: str) -> tuple[float | None, float | None]:
    """Kernel's own view of the negotiated rate, in Mb/s (tx, rx)."""
    rx = tx = None
    mbit = _read(f"/sys/class/net/{iface}/speed")
    if mbit.strip().isdigit():
        tx = float(mbit.strip())
    return tx, rx


def gateway() -> str:
    """Default gateway IP, for latency probes that stay on the LAN."""
    with open(ROUTE_TABLE, "r", encoding="utf-8", errors="replace") as fh:
        next(fh, None)
        for line in fh:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "00000000":
                continue
            gw = int(parts[2], 16)
            if gw == 0:
                continue
            return ".".join(str((gw >> s) & 0xFF) for s in (0, 8, 16, 24))
    return ""


def ping(host: str, count: int = 4, interval: float = 0.2) -> dict | None:
    """Latency probe. Returns min/avg/max/jitter/loss or None if unusable."""
    if not host or not shutil.which("ping"):
        return None
    out = _run(
        ["ping", "-n", "-c", str(count), "-i", str(interval), "-W", "1", host],
        timeout=max(3.0, count * (interval + 1.2)),
    )
    if not out:
        return None

    sent = got = 0
    loss = 100.0
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", out)
    if m:
        sent, got = int(m.group(1)), int(m.group(2))
        loss = 100.0 * (sent - got) / sent if sent else 100.0

    rtts = [float(x) for x in re.findall(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", out)]
    stats = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", out)
    if stats:
        mn, avg, mx, jitter = (float(stats.group(i)) for i in (1, 2, 3, 4))
    elif rtts:
        mn, avg, mx = min(rtts), sum(rtts) / len(rtts), max(rtts)
        jitter = sum(abs(b - a) for a, b in zip(rtts, rtts[1:])) / max(
            1, len(rtts) - 1
        )
    else:
        return {"host": host, "loss": loss, "min": None, "avg": None, "max": None,
                "jitter": None, "sent": sent, "recv": got}

    return {
        "host": host,
        "loss": loss,
        "min": mn,
        "avg": avg,
        "max": mx,
        "jitter": jitter,
        "sent": sent,
        "recv": got,
    }

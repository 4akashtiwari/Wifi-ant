"""A fake radio, so the UI can be developed and demoed without hardware.

Numbers are deliberately plausible: a signal that wanders around a baseline,
slow fades, two MIMO chains that disagree slightly, retries that creep up.
Good enough to shake out chart scaling and the anomaly detector.
"""

from __future__ import annotations

import math
import random
import time

from .radio import RadioSample

NOISE_FLOOR = -94.0


class MockRadio:
    def __init__(self, iface: str = "wlan0", baseline: float = -54.0, seed: int = 7):
        self.iface = iface
        self.baseline = baseline
        self.rng = random.Random(seed)
        self._t0 = time.time()
        self._retries = 0
        self._failed = 0
        self._packets = 0
        self._fade_until = 0.0

    def read(self) -> RadioSample:
        t = time.time() - self._t0

        # slow wander (breathing) + fast jitter
        wander = 3.2 * math.sin(t / 41.0) + 1.4 * math.sin(t / 7.3)
        jitter = self.rng.gauss(0, 0.7)

        # every so often the link drops out for a few seconds
        if t > self._fade_until and self.rng.random() < 0.004:
            self._fade_until = t + self.rng.uniform(2, 6)
        fading = t < self._fade_until

        dip = -self.rng.uniform(10, 16) if fading else 0.0
        signal = self.baseline + wander + jitter + dip

        chains = sorted(
            [round(signal + self.rng.gauss(0, 0.9), 1),
             round(signal + self.rng.gauss(-1.6, 1.1), 1)],
            reverse=True,
        )
        noise = NOISE_FLOOR + self.rng.gauss(0, 0.6)

        # retries only climb; they climb faster when the link is ugly
        if signal < -70:
            self._retries += self.rng.randint(0, 9)
            self._failed += self.rng.randint(0, 2)
        else:
            self._retries += self.rng.randint(0, 2)
        self._packets += self.rng.randint(90, 160)

        rx = max(6.0, 260.0 - abs(signal + 40) * 4.1 + self.rng.gauss(0, 12))
        tx = max(6.0, rx * 0.9 + self.rng.gauss(0, 9))

        return RadioSample(
            iface=self.iface,
            ssid="mocknet",
            bssid="02:00:5e:10:00:01",
            freq_mhz=5180,
            signal_dbm=round(signal, 1),
            noise_dbm=round(noise, 1),
            snr_db=round(signal - noise, 1),
            chains=chains,
            rx_bitrate=round(rx, 1),
            tx_bitrate=round(tx, 1),
            tx_retries=self._retries,
            tx_failed=self._failed,
            tx_packets=self._packets,
            connected=True,
            ts=time.time(),
            source="mock",
        )

    def ping(self, host: str = "192.168.1.1") -> dict:
        base = 3.0 + max(0.0, (-self.baseline + self.rng.gauss(0, 3)) * 0.35)
        rtts = [max(0.4, base + self.rng.gauss(0, 1.2)) for _ in range(4)]
        if self.rng.random() < 0.05:
            rtts[0] = None
        good = [r for r in rtts if r]
        loss = 100.0 * (len(rtts) - len(good)) / len(rtts)
        if not good:
            return {"host": host, "loss": 100.0, "min": None, "avg": None,
                    "max": None, "jitter": None, "sent": 4, "recv": 0}
        return {
            "host": host,
            "loss": loss,
            "min": min(good),
            "avg": sum(good) / len(good),
            "max": max(good),
            "jitter": max(good) - min(good),
            "sent": 4,
            "recv": len(good),
        }


def simulate_speed() -> float:
    return max(1.0, 140 + random.uniform(-30, 30))

# wifiant

A live antenna lab for the Wi-Fi link you're already on.

You're building an antenna from scratch. That means a lot of "did that just
help, or did the wall move?" — and `wavemon` only shows you one number, right
now, with no memory. `wifiant` keeps the history, tells you whether a change is
real, and remembers every revision you've built so you can compare them later
without trusting your memory.

```
┌ signal  -52.0 dBm  +3.3 vs window low  wlan0 ────────────────┐
│ 10s -53.2  spread 4.0   trend +12.1 dB/min   stable 95%      │
│ ▁▂▃▅▄▆▅▇▆▅▄▃▄▅▆▇▆▅▄▅▆▇▆▅▄▅▆▇▆▅▄▃▄▅▆▇▆▅▄▅▆▇▆▅▄▅▆▇▆▅▄▅▆       │
└──────────────────────────────────────────────────────────────┘
```

## Why not just wavemon

`wavemon` shows the *current* value from the connected AP. An antenna build
needs three things it doesn't give you:

| what you need | wavemon | wifiant |
| --- | --- | --- |
| history you can look back at | single value | rolling windows 10s/1m/5m/session |
| is this change real or noise | — | spread, σ, stability %, outlier flags |
| which revision was better | — | revisions + recorded runs + verdict |
| latency and real throughput | — | gateway ping + on-demand speed probe |
| was it my antenna or my position | — | runs are recorded per revision |
| did *this one trim* help | — | tare: zero here, read deltas |
| when is my link actually good | — | `watch` a whole day, `analyze` it |

## Install

One line, no sudo, nothing written outside your home directory:

```sh
curl -fsSL https://raw.githubusercontent.com/4akashtiwari/Wifi-ant/main/install.sh | bash
```

Or clone and run it yourself if you'd rather read it first:

```sh
git clone https://github.com/4akashtiwari/Wifi-ant
cd Wifi-ant
./install.sh
```

Either way you get a private venv at `~/.local/share/wifiant/venv` and a
`wifiant` launcher in `~/.local/bin`. If that directory isn't on your `PATH`,
the script tells you the one line to add.

```sh
./install.sh --system        # /usr/local, needs sudo
./install.sh --dev           # editable install + pytest
./install.sh --systemd       # also install a daily logger as a user unit
./install.sh --uninstall     # removes the venv, keeps your recorded runs
```

`--uninstall` deliberately leaves `designs/` and `watch/` alone. Months of
measurements shouldn't disappear because you tidied up a venv.

### Manual install

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

Needs Python 3.10+, Linux, and ideally `iw` (`sudo apt install iw`). It falls
back to `/proc/net/wireless`, then `nmcli`, then a simulated radio for demos.
No root needed for watching. `sweep --rescan` wants root.

## Use it

```sh
wifiant                 # live view
wifiant --demo          # simulated radio, if you just want to look around
wifiant --iface wlp2s0  # watch a specific interface
```

### Keys in the live view

| key | does |
| --- | --- |
| `r` | start / stop a recording for the current revision |
| `s` | list recorded runs; mark two, `c` compares them |
| `c` | compare the two most recent runs |
| `v` | overlay the best previous run on the live chart |
| `f` | focus mode — one number, one trace, nothing else |
| `d` | switch revision |
| `a` | new revision |
| `x` | throughput probe (downloads ~25 MB, counts, discards) |
| `l` | quick log: best, median, outliers |
| `p` | ping the gateway right now |
| `t` | tare — zero the reading here, so the next trim reads as `+0.6 dB` |
| `m` | smoothing on/off (10-sample trailing average drawn over the raw trace) |
| `n` | auto-record — keeps saving 10-minute segments until you turn it off |
| `⇧↑`/`⇧↓` | tighten/loosen the chart's y window |

## Revisions and runs

Every revision is a folder under `~/.local/share/wifiant/designs/`:

```
designs/
  v3-folded-dipole/
    design.json          # name, description, geometry notes
    notes.md             # your write-up, free text
    sessions/
      20251112-164512-a1b2.json    # one recorded run
```

A session is per-second means plus a start time, so a run is small and diffable.
Point `WIFIANT_HOME` elsewhere to keep the lab next to the build photos (or in
git).

```sh
wifiant new "v4 - shorter elements" -d "cut 4mm off each arm"
wifiant record v4-shorter-elements --seconds 120
wifiant sessions v4-shorter-elements
wifiant compare 20251112-1645 20251112-1702
```

`compare` normalises both runs to the shorter one's length, then prints the
verdict — including the "it's just noise" answer, which is the one that saves
you from chasing ghosts:

```
A = v4-shorter-elements / 20251112-1645
mean -58.2   median -58.0   jitter 1.4   stable 96%
B = v4-shorter-elements / 20251112-1702
mean -54.1   median -54.0   jitter 1.1   stable 98%

B wins by 4.0 dB (3.6 sigma)
```

### Tare: measuring one trim at a time

The bit that actually helps at the bench. Sit the antenna in a jig, press `t`,
and the big number switches from dBm to **delta from that spot**. Bend an
element, watch it go `+0.6 dB`. Bend it back, watch it go `-0.2`. No mental
arithmetic, and the chart still shows the absolute trace underneath.

Tare is a *reading aid*, not a calibration — it answers "did that change
anything", not "what is my gain".

### Headless

```sh
wifiant record v4 --seconds 300        # cron/ssh friendly
wifiant stats                          # one shot
wifiant history --seconds 60           # sample and plot in the terminal
wifiant config                         # JSON, for a tmux status bar/script
wifiant sweep --rescan                 # what the antenna can see, best first
wifiant export -o sessions.csv         # every recorded second, for pandas/R
```

### Watching a whole day

`record` is for a session at the bench. `watch` is the other timescale — leave
it running and find out when your link is actually good:

```sh
wifiant watch --design v4 --interval 60      # until ^C, one row per minute
wifiant watch --design v4 --duration 43200   # or 12 h
wifiant analyze                              # what the day looked like
```

One file per day under `~/.local/share/wifiant/watch/`, one line per interval
(`mean`, `lo`, `hi`, `noise`, `snr`, timestamp). `analyze` prints the mean
signal and noise floor per hour, the best one-hour window, and any run of
consecutive low samples:

```
mean signal by hour
00 ██████████  -51   01 ██████████  -51   02 ███████████ -50
...
15 ████        -58   16 ██████      -55   17 █████████   -52

dips (runs at least 6 dB below median)
start            length   low
12 Nov 15:04     22m      -58.9
```

That afternoon row is usually the neighbours coming home, not your antenna.
Worth knowing before you rebuild a good design.

## What it actually measures

- **signal / noise / SNR / per-chain** from `iw dev <if>. link` and the station
  dump — sampled every second, kept in a bounded ring.
- **trend** is a least-squares slope in dBm/min. Useful while you're bending
  an element and watching the number in real time.
- **stability** is the share of samples within ±1.5 dB of the window median.
  A higher gain with a worse stability score is not an upgrade on a moving link.
- **smoothing** is a *trailing* moving average, deliberately not centred: while
  you're holding an element you want the line to follow what just happened,
  not to quietly rewrite the past.
- **outliers** use median + MAD, not mean + σ, because a fade dip would
  otherwise hide itself inside the average it drags down.
- **throughput** is a real download (Cloudflare's speed endpoint by default,
  `--url` to change it). The radio's own "bitrate" is a negotiated PHY rate —
  it's shown, labelled as such, and should not be read as a speed test.
- **gaps are kept.** When the link drops, the sampler writes nothing rather
  than a fake zero, and the coverage percentage in the stats panel shows it.

## Caveats worth knowing

- dBm from a driver is rounded and smoothed. Trust 2–3 dB, not 0.5 dB, unless
  you're averaging a few hundred samples (which the windows do for you).
- Don't tune against a single number while you're also moving. Record a run,
  put the laptop down, then compare runs. The `stable %` figure is there to
  tell you the difference between the two situations.
- The signal number follows the AP you're associated with. If your router
  band-steers you to another channel mid-run, that's a new experiment; the
  session records the SSID/BSSID so you can spot it.

## Layout

```
install.sh      zero-sudo installer (venv + launcher, --uninstall keeps data)
wifiant/
  radio.py      read-only sampling: iw, /proc/net/wireless, nmcli, ping
  collector.py  background sampler thread + ring history
  stats.py      windows, robust anomalies, quality score
  designs.py    revisions, recorded runs, comparisons
  chart.py      braille plots (2x4 dots per cell), smoothing, hourly bars
  watch.py      day-scale logging and analysis
  app.py        the TUI
  cli.py        subcommands
  scan.py       nearby APs
  speedtest.py  throughput probe
  mock.py       fake radio for --demo
```

## Tests

```sh
pip install -e ".[dev]"
pytest
```

## Licence

MIT.

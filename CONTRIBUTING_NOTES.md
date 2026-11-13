# Build notes

Scratch file for decisions that don't belong in the README. Delete when the
repo grows real contributor docs.

- Sampling is one thread, UI paints on its own timer. A hung `iw` call must
  never freeze the interface, so nothing in `app.py` shells out.
- Gaps are data. If the link drops, no sample is written and the coverage
  figure falls. Writing `-100` there would quietly corrupt every average.
- Anomalies use median/MAD. A fade dip drags a mean down and then hides inside
  its own inflated sigma.
- Verdict thresholds: under 0.5 dB is called noise even if sigma math says
  otherwise; sigma is reported so it can be argued with.
- Overlays are time-aligned by duration, not absolute clock time, and always
  labelled. Stretching a 5 s run across a 2 min axis was misleading.
- Tare is read-only bookkeeping in the app; it never touches the sampler, so
  the recorded absolute values stay comparable across runs.
- Auto-record splits at 10 minutes rather than one giant run: sessions are
  compared on a common window, and a 6-hour blob makes that window useless.
- Comparison refuses a verdict under 10 samples. Sigma on three points is
  noise dressed up as statistics.
- Band colours live in ui.py, not scattered through widgets. If a threshold
  changes it should change in one place: -50 excellent, -60 good, -67 fair,
  -75 weak, below that poor. These are comfortable round numbers, not physics.
- The main screen degrades by dropping information, never by wrapping. Under
  110 cols the percentile columns go, under 34 rows the band legend goes,
  under 32 rows the header loses its sparkline.

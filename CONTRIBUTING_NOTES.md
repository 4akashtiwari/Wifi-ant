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

import time

import pytest

from wifiant import designs, radio
from wifiant.chart import _bits_for, auto_range, plot
from wifiant.stats import History, Sample


# -- history ------------------------------------------------------------
def make_history(n=120, step=1.0, base=-60.0):
    h = History()
    t0 = time.time() - n * step
    for i in range(n):
        h.add(Sample(ts=t0 + i * step, signal=base, snr=30))
    return h


def test_window_and_stats():
    h = make_history(60)
    st = h.stats(30)
    assert 28 <= st.count <= 31
    assert st.mean == pytest.approx(-60.0)
    assert st.jitter == pytest.approx(0.0)


def test_trend_sign():
    h = History()
    t0 = time.time() - 60
    for i in range(60):
        h.add(Sample(ts=t0 + i, signal=-70 + i * 0.5))  # climbing
    assert h.trend(60) > 20  # dB per minute


def test_best_and_quality():
    h = History()
    t0 = time.time() - 10
    for i, v in enumerate([-80, -50, -90, -70]):
        h.add(Sample(ts=t0 + i, signal=v))
    assert h.best().signal == -50
    assert 0 <= Sample(ts=0, signal=-50, snr=40).quality <= 100
    weak = Sample(ts=0, signal=-88, snr=5).quality
    strong = Sample(ts=0, signal=-45, snr=45).quality
    assert strong > weak


def test_anomalies_ignore_a_steady_line():
    h = make_history(60, base=-60)
    h.add(Sample(ts=time.time(), signal=-60.4))
    assert h.anomalies(3600) == []


def test_coverage_reports_gaps():
    h = make_history(60)
    assert h.coverage(60) == pytest.approx(1.0, abs=0.05)
    sparse = History()
    t0 = time.time() - 60
    for i in range(6):  # one sample every 10 s
        sparse.add(Sample(ts=t0 + i * 10, signal=-60))
    assert sparse.coverage(60) < 0.2


# -- charts -------------------------------------------------------------
def test_auto_range_has_minimum_span():
    lo, hi = auto_range([-61, -60, -59])
    assert hi - lo >= 8.0


def test_plot_uses_braille_and_axis():
    text = plot([-60, -55, -70, -50] * 10, width=40, height=6)
    plain = text.plain
    assert any(0x2800 <= ord(c) <= 0x28FF for c in plain)
    assert len(plain.splitlines()) == 6


def test_resample_keeps_extremes_reasonable():
    bits = _bits_for([-60, -55, -50] * 200, width=20, height=5, lo=-80, hi=-40)
    assert len(bits) == 20  # 5 rows * 4 dots
    assert any(any(row) for row in bits)


# -- lab / designs ------------------------------------------------------
def test_design_and_session_roundtrip(tmp_path):
    lab = designs.Lab(tmp_path)
    d = lab.create("v2 - folded", "shorter elements")
    assert lab.path(d.slug).is_dir()
    assert (lab.path(d.slug) / "design.json").exists()

    rec = designs.Recorder(d.slug)
    t0 = time.time()
    for i in range(10):
        rec.feed(Sample(ts=t0 + i, signal=-58 - (i % 3)))
    meta = rec.stop("first pass")
    lab.save_session(meta)

    assert len(lab.sessions(d.slug)) == 1
    st = lab.sessions(d.slug)[0].stats()
    assert st.count > 0
    assert meta.note == "first pass"


def test_duplicate_design_names_get_distinct_slugs(tmp_path):
    lab = designs.Lab(tmp_path)
    a = lab.create("same name")
    b = lab.create("same name")
    assert a.slug != b.slug
    assert len(lab.designs()) == 2


def test_compare_picks_a_winner(tmp_path):
    lab = designs.Lab(tmp_path)
    d = lab.create("rev")
    good = designs.SessionMeta(sid="good", design=d.slug, started=0,
                              series=[[i, -50.0, 1] for i in range(30)])
    bad = designs.SessionMeta(sid="bad", design=d.slug, started=0,
                             series=[[i, -58.0, 1] for i in range(30)])
    cmp = lab.compare(good, bad)
    # delta is B relative to A, so a better A gives a negative delta
    assert cmp.delta == pytest.approx(-8.0)
    assert "A wins" in cmp.verdict()

    flipped = lab.compare(bad, good)
    assert flipped.delta == pytest.approx(8.0)
    assert "B wins" in flipped.verdict()


def test_compare_flat_is_called_noise(tmp_path):
    lab = designs.Lab(tmp_path)
    a = designs.SessionMeta(sid="a", design="x", started=0,
                           series=[[i, -50.0, 1] for i in range(20)])
    b = designs.SessionMeta(sid="b", design="x", started=0,
                           series=[[i, -50.2, 1] for i in range(20)])
    assert "noise" in lab.compare(a, b).verdict()


# -- radio parsing ------------------------------------------------------
def test_gateway_is_parsed():
    gw = radio.gateway()
    assert gw == "" or gw.count(".") == 3


def test_pick_iface_prefers_explicit():
    assert radio.pick_iface("wlan9") == "wlan9"

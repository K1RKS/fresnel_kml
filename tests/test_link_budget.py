import math

import pytest

from fresnelkml.analysis import Site, analyze
from fresnelkml.link_budget import (
    clearance_to_nu,
    direction_snr,
    effective_gain_dbi,
    fspl_db,
    knife_edge_loss_db,
    link_snr,
    worst_clearance,
)


def test_fspl_10km_5_8ghz():
    # FSPL(dB) = 20 log10(d_km) + 20 log10(f_mhz) + 32.44
    expected = 20 * math.log10(10) + 20 * math.log10(5800) + 32.44
    assert fspl_db(10_000, 5.8e9) == pytest.approx(expected, abs=0.05)


def test_effective_gain_omni_and_boresight():
    g, notes = effective_gain_dbi(None, None, None, None)
    assert g == 0.0 and "0 dBi" in notes[0]
    g, _ = effective_gain_dbi(25.0, 360, 90, 10)
    assert g == 25.0
    g, _ = effective_gain_dbi(25.0, 8.0, 0.0, 0.0)
    assert g == 25.0


def test_effective_gain_off_boresight():
    # Half beamwidth azimuth: 12 * (0.5)^2 = 3 dB down
    g, _ = effective_gain_dbi(25.0, 8.0, 4.0, 0.0)
    assert g == pytest.approx(22.0)
    g, _ = effective_gain_dbi(25.0, 8.0, 40.0, 0.0)
    assert g == pytest.approx(-5.0)  # floor at G0 − 30


def test_clearance_nu_and_knife_edge():
    assert clearance_to_nu(0.6) == pytest.approx(-0.6 * math.sqrt(2))
    assert knife_edge_loss_db(0.6) == 0.0
    assert knife_edge_loss_db(1.0) == 0.0
    assert knife_edge_loss_db(None) is None
    loss0 = knife_edge_loss_db(0.0)
    assert loss0 == pytest.approx(6.9 + 20 * math.log10(math.sqrt(0.1 ** 2 + 1) - 0.1), abs=0.05)
    assert knife_edge_loss_db(-1.0) > loss0


def test_worst_clearance():
    assert worst_clearance(0.8, 0.2, None) == 0.2
    assert worst_clearance(None, None) is None


def test_direction_asymmetric():
    d = direction_snr(
        tx_power_dbm=27, rx_noise_dbm=-95,
        gain_tx_dbi=10, gain_rx_dbi=25,
        beamwidth_tx=360, beamwidth_rx=8,
        azimuth_tx=None, azimuth_rx=90,
        elevation_tx=None, elevation_rx=0,
        bearing_tx=100, bearing_rx=280,
        tilt_tx=0, tilt_rx=0,
        distance_m=10_000, freq_hz=5.8e9, clearance=0.8)
    assert d["estimated_snr_db"] == d["optimal_snr_db"]
    assert d["obstruction_loss_db"] == 0.0
    blocked = direction_snr(
        tx_power_dbm=27, rx_noise_dbm=-95,
        gain_tx_dbi=10, gain_rx_dbi=25,
        beamwidth_tx=None, beamwidth_rx=None,
        azimuth_tx=None, azimuth_rx=None,
        elevation_tx=None, elevation_rx=None,
        bearing_tx=0, bearing_rx=180,
        tilt_tx=0, tilt_rx=0,
        distance_m=10_000, freq_hz=5.8e9, clearance=-0.5)
    assert blocked["estimated_snr_db"] < blocked["optimal_snr_db"]
    assert blocked["obstruction_loss_db"] > 0


def test_link_snr_both_ways():
    a = Site("A", 45.0, -122.0, asl_m=100, tx_power_dbm=30, noise_dbm=-90, gain_dbi=5)
    b = Site("B", 45.1, -122.0, asl_m=120, tx_power_dbm=20, noise_dbm=-100, gain_dbi=20)
    out = link_snr(a, b, distance_m=11_000, freq_hz=5.8e9,
                   bearing_ab=0, bearing_ba=180, tilt_ab=0.1, tilt_ba=-0.1, clearance=None)
    assert out["ab"]["tx_power_dbm"] == 30 and out["ab"]["noise_dbm"] == -100
    assert out["ba"]["tx_power_dbm"] == 20 and out["ba"]["noise_dbm"] == -90
    assert out["ab"]["estimated_snr_db"] is None
    assert out["ab"]["optimal_snr_db"] is not None


def test_analyze_includes_snr(local_config):
    a = Site("A", 37.0, -122.0, asl_m=150, tx_power_dbm=27, noise_dbm=-95, gain_dbi=10)
    b = Site("B", 37.05, -122.0, asl_m=180, tx_power_dbm=27, noise_dbm=-95, gain_dbi=25)
    from fresnelkml.config import load_config, build_chain
    cfg = load_config(env={"FRESNEL_CONFIG": local_config})
    report = analyze(a, b, elevation=build_chain(cfg), config=cfg)
    assert report.snr is not None
    d = report.to_dict()["snr"]
    assert d["ab"]["optimal_snr_db"] is not None
    assert d["ba"]["optimal_snr_db"] is not None
    if report.terrain_checked and report.min_clearance is not None:
        assert d["ab"]["estimated_snr_db"] is not None

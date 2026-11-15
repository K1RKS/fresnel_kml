"""Free-space link budget and Fresnel-obstruction SNR estimates."""

from __future__ import annotations

import math
from typing import Any

DEFAULT_TX_POWER_DBM = 27.0
DEFAULT_NOISE_DBM = -95.0
DEFAULT_GAIN_DBI = 0.0
MIN_NOISE_DBM = -120.0
MAX_NOISE_DBM = -50.0
MIN_TX_POWER_DBM = 0.0
MAX_TX_POWER_DBM = 40.0
# ITU-R P.526: diffraction loss is negligible for ν ≤ −0.78 (~0.55·r1 clearance).
KNIFE_EDGE_ZERO_NU = -0.78
GAIN_FLOOR_DB = 30.0  # never subtract more than this from boresight gain for aim


def fspl_db(distance_m: float, freq_hz: float) -> float:
    """Free-space path loss in dB."""
    if distance_m <= 0 or freq_hz <= 0:
        raise ValueError("distance and frequency must be positive")
    return 20.0 * math.log10(distance_m) + 20.0 * math.log10(freq_hz) - 147.55


def azimuth_offset_deg(azimuth_deg: float | None, bearing_deg: float) -> float | None:
    """Absolute degrees between antenna azimuth and path bearing, or None if azimuth unknown."""
    if azimuth_deg is None:
        return None
    return abs(((azimuth_deg - bearing_deg + 540.0) % 360.0) - 180.0)


def elevation_offset_deg(elevation_deg: float | None, path_tilt_deg: float) -> float | None:
    """Absolute degrees between antenna elevation setting and path tilt, or None if unset."""
    if elevation_deg is None:
        return None
    return abs(elevation_deg - path_tilt_deg)


def is_directional(beamwidth_deg: float | None) -> bool:
    return beamwidth_deg is not None and 0 < beamwidth_deg < 360


def effective_gain_dbi(gain_dbi: float | None, beamwidth_deg: float | None,
                      az_off_deg: float | None, el_off_deg: float | None) -> tuple[float, list[str]]:
    """Boresight gain reduced by a simple Gaussian pattern from beamwidth and aim offsets."""
    notes: list[str] = []
    g0 = DEFAULT_GAIN_DBI if gain_dbi is None else float(gain_dbi)
    if gain_dbi is None:
        notes.append("gain defaulted to 0 dBi")
    if not is_directional(beamwidth_deg):
        return g0, notes
    bw = float(beamwidth_deg)
    az = 0.0 if az_off_deg is None else float(az_off_deg)
    el = 0.0 if el_off_deg is None else float(el_off_deg)
    loss = 12.0 * ((az / bw) ** 2 + (el / bw) ** 2)
    return g0 - min(loss, GAIN_FLOOR_DB), notes


def clearance_to_nu(clearance: float) -> float:
    """Fresnel-Kirchhoff diffraction parameter ν from clearance in units of r1.

    clearance = (LOS − obstacle) / r1; positive is clear. ν = −clearance · √2.
    """
    return -clearance * math.sqrt(2.0)


def knife_edge_loss_db(clearance: float | None) -> float | None:
    """ITU-R P.526 approximate knife-edge diffraction loss (dB) from Fresnel clearance × r1."""
    if clearance is None:
        return None
    nu = clearance_to_nu(clearance)
    if nu <= KNIFE_EDGE_ZERO_NU:
        return 0.0
    return 6.9 + 20.0 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1)


def worst_clearance(*values: float | None) -> float | None:
    """Most pessimistic (lowest) clearance among the given values."""
    known = [v for v in values if v is not None]
    return min(known) if known else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def direction_snr(*, tx_power_dbm: float, rx_noise_dbm: float,
                  gain_tx_dbi: float | None, gain_rx_dbi: float | None,
                  beamwidth_tx: float | None, beamwidth_rx: float | None,
                  azimuth_tx: float | None, azimuth_rx: float | None,
                  elevation_tx: float | None, elevation_rx: float | None,
                  bearing_tx: float, bearing_rx: float,
                  tilt_tx: float, tilt_rx: float,
                  distance_m: float, freq_hz: float,
                  clearance: float | None) -> dict[str, Any]:
    """One-way link budget: optimal (clear Fresnel) and estimated (with obstruction loss)."""
    assumptions: list[str] = []
    az_tx = azimuth_offset_deg(azimuth_tx, bearing_tx)
    az_rx = azimuth_offset_deg(azimuth_rx, bearing_rx)
    el_tx = elevation_offset_deg(elevation_tx, tilt_tx)
    el_rx = elevation_offset_deg(elevation_rx, tilt_rx)
    g_tx, n_tx = effective_gain_dbi(gain_tx_dbi, beamwidth_tx, az_tx, el_tx)
    g_rx, n_rx = effective_gain_dbi(gain_rx_dbi, beamwidth_rx, az_rx, el_rx)
    assumptions.extend(f"TX {n}" for n in n_tx)
    assumptions.extend(f"RX {n}" for n in n_rx)

    fspl = fspl_db(distance_m, freq_hz)
    obst = knife_edge_loss_db(clearance)
    rx_opt = tx_power_dbm + g_tx + g_rx - fspl
    snr_opt = rx_opt - rx_noise_dbm
    if obst is None:
        rx_est = snr_est = None
    else:
        rx_est = rx_opt - obst
        snr_est = rx_est - rx_noise_dbm

    return {
        "optimal_snr_db": _round(snr_opt),
        "estimated_snr_db": _round(snr_est),
        "rx_dbm_optimal": _round(rx_opt),
        "rx_dbm_estimated": _round(rx_est),
        "fspl_db": _round(fspl),
        "obstruction_loss_db": _round(obst),
        "clearance_used": None if clearance is None else round(clearance, 3),
        "tx_power_dbm": _round(tx_power_dbm),
        "noise_dbm": _round(rx_noise_dbm),
        "gain_tx_dbi": _round(g_tx),
        "gain_rx_dbi": _round(g_rx),
        "assumptions": assumptions,
    }


def link_snr(site_a, site_b, *, distance_m: float, freq_hz: float,
             bearing_ab: float, bearing_ba: float, tilt_ab: float, tilt_ba: float,
             clearance: float | None) -> dict[str, Any]:
    """Bidirectional SNR from two Site objects (RF fields optional; defaults applied)."""
    def power(site) -> float:
        v = getattr(site, "tx_power_dbm", None)
        return DEFAULT_TX_POWER_DBM if v is None else float(v)

    def noise(site) -> float:
        v = getattr(site, "noise_dbm", None)
        return DEFAULT_NOISE_DBM if v is None else float(v)

    common = dict(distance_m=distance_m, freq_hz=freq_hz, clearance=clearance)
    ab = direction_snr(
        tx_power_dbm=power(site_a), rx_noise_dbm=noise(site_b),
        gain_tx_dbi=site_a.gain_dbi, gain_rx_dbi=site_b.gain_dbi,
        beamwidth_tx=site_a.beamwidth_deg, beamwidth_rx=site_b.beamwidth_deg,
        azimuth_tx=site_a.azimuth_deg, azimuth_rx=site_b.azimuth_deg,
        elevation_tx=site_a.elevation_deg, elevation_rx=site_b.elevation_deg,
        bearing_tx=bearing_ab, bearing_rx=bearing_ba,
        tilt_tx=tilt_ab, tilt_rx=tilt_ba, **common)
    ba = direction_snr(
        tx_power_dbm=power(site_b), rx_noise_dbm=noise(site_a),
        gain_tx_dbi=site_b.gain_dbi, gain_rx_dbi=site_a.gain_dbi,
        beamwidth_tx=site_b.beamwidth_deg, beamwidth_rx=site_a.beamwidth_deg,
        azimuth_tx=site_b.azimuth_deg, azimuth_rx=site_a.azimuth_deg,
        elevation_tx=site_b.elevation_deg, elevation_rx=site_a.elevation_deg,
        bearing_tx=bearing_ba, bearing_rx=bearing_ab,
        tilt_tx=tilt_ba, tilt_rx=tilt_ab, **common)
    return {"ab": ab, "ba": ba}

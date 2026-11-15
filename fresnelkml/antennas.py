"""Standard 5.8 GHz Wi‑Fi / AREDN antenna patterns for SNR gain defaults."""

from __future__ import annotations

from typing import Any

DEFAULT_PATTERN_ID = "omni-6"

# Typical Ubiquiti / MikroTik-class 5 GHz antennas used on mesh links.
PATTERNS: list[dict[str, Any]] = [
    {"id": "omni-2", "label": "Omni 2 dBi (stock / rubber duck)", "gain_dbi": 2.0, "beamwidth_deg": 360.0},
    {"id": "omni-6", "label": "Omni 6 dBi", "gain_dbi": 6.0, "beamwidth_deg": 360.0},
    {"id": "omni-10", "label": "Omni 10 dBi (e.g. Ubiquiti AMO-5G10)", "gain_dbi": 10.0, "beamwidth_deg": 360.0},
    {"id": "omni-13", "label": "Omni 13 dBi (e.g. Ubiquiti AMO-5G13)", "gain_dbi": 13.0, "beamwidth_deg": 360.0},
    {"id": "sector-16", "label": "Sector 16 dBi / ~120\u00b0", "gain_dbi": 16.0, "beamwidth_deg": 120.0},
    {"id": "sector-19", "label": "Sector 19 dBi / ~120\u00b0", "gain_dbi": 19.0, "beamwidth_deg": 120.0},
    {"id": "panel-16", "label": "Panel / NanoStation ~16 dBi", "gain_dbi": 16.0, "beamwidth_deg": 40.0},
    {"id": "dish-19", "label": "Dish / NanoBeam ~19 dBi", "gain_dbi": 19.0, "beamwidth_deg": 30.0},
    {"id": "dish-24", "label": "Dish / MikroTik LHG 24 dBi", "gain_dbi": 24.0, "beamwidth_deg": 7.0},
    {"id": "dish-25", "label": "Dish 25 dBi", "gain_dbi": 25.0, "beamwidth_deg": 8.0},
    {"id": "dish-27", "label": "Dish / MikroTik LHG XL 27 dBi", "gain_dbi": 27.0, "beamwidth_deg": 7.0},
    {"id": "dish-30", "label": "Dish / RocketDish 30 dBi", "gain_dbi": 30.0, "beamwidth_deg": 5.0},
    {"id": "dish-34", "label": "Dish / RocketDish 34 dBi", "gain_dbi": 34.0, "beamwidth_deg": 3.0},
]

_BY_ID = {p["id"]: p for p in PATTERNS}


def pattern_by_id(pattern_id: str | None) -> dict[str, Any]:
    """Return a catalog entry, or the default Omni 6 dBi pattern."""
    if pattern_id and pattern_id in _BY_ID:
        return dict(_BY_ID[pattern_id])
    return dict(_BY_ID[DEFAULT_PATTERN_ID])


def default_gain_dbi() -> float:
    return float(_BY_ID[DEFAULT_PATTERN_ID]["gain_dbi"])


def match_pattern(gain_dbi: float, beamwidth_deg: float | None = None) -> dict[str, Any]:
    """Nearest catalog entry by gain; on a tie prefer closer beamwidth when given."""
    gain = float(gain_dbi)
    best = PATTERNS[0]
    best_key = (abs(best["gain_dbi"] - gain), 0.0)
    for p in PATTERNS:
        dg = abs(p["gain_dbi"] - gain)
        db = 0.0
        if beamwidth_deg is not None:
            db = abs(float(p["beamwidth_deg"]) - float(beamwidth_deg))
        key = (dg, db)
        if key < best_key:
            best, best_key = p, key
    return dict(best)


def public_patterns() -> list[dict[str, Any]]:
    """JSON-ready catalog for the web UI."""
    return [dict(p) for p in PATTERNS]

"""Fresnel zone and earth-bulge formulas (spec section 1)."""

from __future__ import annotations

import math

SPEED_OF_LIGHT = 299_792_458.0
EARTH_RADIUS_M = 6_371_000.0
DEFAULT_K = 4.0 / 3.0


def wavelength(freq_hz: float) -> float:
    if freq_hz <= 0:
        raise ValueError("frequency must be positive")
    return SPEED_OF_LIGHT / freq_hz


def zone_radius(d1: float, d2: float, wavelength_m: float, n: int = 1) -> float:
    """Radius of the nth Fresnel zone at a point d1 from one end and d2 from the other."""
    total = d1 + d2
    if total <= 0 or d1 <= 0 or d2 <= 0:
        return 0.0
    return math.sqrt(n * wavelength_m * d1 * d2 / total)


def max_radius(distance_m: float, wavelength_m: float, n: int = 1) -> float:
    """Zone radius at the midpoint of the path."""
    return 0.5 * math.sqrt(n * wavelength_m * distance_m)


def earth_bulge(d1: float, d2: float, k: float = DEFAULT_K, radius_m: float = EARTH_RADIUS_M) -> float:
    """Height of the earth's curvature above the chord at d1/d2, for effective-earth factor k."""
    return d1 * d2 / (2.0 * k * radius_m)

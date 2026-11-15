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


def path_tilt(distance_m: float, height_a_m: float, height_b_m: float,
              radius_m: float = EARTH_RADIUS_M) -> tuple[float, float]:
    """Elevation of the straight line to the other antenna, in degrees above the local horizontal.

    Returns (tilt at A toward B, tilt at B toward A). Positive is uptilt, negative is downtilt.
    Heights are above the same datum (sea level). This is the geometric aim, on the true earth
    radius with no refraction, so it is the tilt to set on the antenna rather than the k-factor ray.
    """
    if distance_m <= 0:
        raise ValueError("distance must be positive")
    phi = distance_m / radius_m
    sin_phi, cos_phi = math.sin(phi), math.cos(phi)

    def elev(near: float, far: float) -> float:
        radial_near = radius_m + near
        radial_far = radius_m + far
        # Local horizontal is the tangent; local up is the radius. The angle of the
        # chord above that tangent is atan2(radial component, tangential component).
        return math.degrees(math.atan2(radial_far * cos_phi - radial_near, radial_far * sin_phi))

    return elev(height_a_m, height_b_m), elev(height_b_m, height_a_m)

import math

import pytest

from fresnelkml import fresnel

LAM = fresnel.wavelength(5.8e9)


def test_t1_wavelength():
    assert LAM == pytest.approx(0.05168835, abs=1e-8)


def test_t2_10km_midpoint():
    r1 = fresnel.zone_radius(5000, 5000, LAM)
    assert r1 == pytest.approx(11.3675, abs=0.001)
    assert 0.6 * r1 == pytest.approx(6.8205, abs=0.001)
    assert fresnel.earth_bulge(5000, 5000) == pytest.approx(1.4715, abs=0.0005)
    assert fresnel.max_radius(10_000, LAM) == pytest.approx(r1)


def test_t3_1km_midpoint():
    assert fresnel.zone_radius(500, 500, LAM) == pytest.approx(3.5947, abs=0.001)


def test_t4_50km_midpoint():
    assert fresnel.zone_radius(25_000, 25_000, LAM) == pytest.approx(25.4186, abs=0.001)
    assert fresnel.earth_bulge(25_000, 25_000) == pytest.approx(36.788, abs=0.001)


def test_t5_off_midpoint():
    assert fresnel.zone_radius(2500, 7500, LAM) == pytest.approx(9.8446, abs=0.001)


def test_handy_form_matches():
    d1, d2, f = 3.0, 7.0, 5.8
    handy = 17.32 * (d1 * d2 / (f * (d1 + d2))) ** 0.5
    assert fresnel.zone_radius(d1 * 1000, d2 * 1000, LAM) == pytest.approx(handy, rel=1e-3)


def test_endpoints_have_zero_radius():
    assert fresnel.zone_radius(0, 1000, LAM) == 0.0
    assert fresnel.zone_radius(1000, 0, LAM) == 0.0


def test_second_zone_is_sqrt2_larger():
    assert fresnel.zone_radius(5000, 5000, LAM, 2) == pytest.approx(fresnel.zone_radius(5000, 5000, LAM) * 2 ** 0.5)


def test_equal_heights_look_down():
    # Each end looks below level by about half the subtended angle, D / (2R).
    d = 10_000
    drop = math.degrees(d / (2 * fresnel.EARTH_RADIUS_M))
    ab, ba = fresnel.path_tilt(d, 0.0, 0.0)
    assert ab == pytest.approx(-drop, abs=1e-4)
    assert ba == pytest.approx(ab)
    assert ab == pytest.approx(-0.0450, abs=1e-3)


def test_higher_far_end_is_uptilt():
    # 100 m rise over 10 km, minus the ~7.8 m the earth drops away.
    d, rise = 10_000.0, 100.0
    drop = d / (2 * fresnel.EARTH_RADIUS_M)
    ab, ba = fresnel.path_tilt(d, 0.0, rise)
    assert ab == pytest.approx(math.degrees(math.atan2(rise, d) - drop), abs=1e-3)
    assert ba == pytest.approx(math.degrees(math.atan2(-rise, d) - drop), abs=1e-3)
    assert ab == pytest.approx(0.528, abs=0.002)
    assert ba == pytest.approx(-0.618, abs=0.002)


def test_tilt_swaps_when_the_ends_swap():
    ab, ba = fresnel.path_tilt(8_000, 40.0, 5.0)
    swapped_ab, swapped_ba = fresnel.path_tilt(8_000, 5.0, 40.0)
    assert ab == pytest.approx(swapped_ba)
    assert ba == pytest.approx(swapped_ab)

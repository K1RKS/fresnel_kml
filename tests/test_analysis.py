import pytest

from fresnelkml import Options, Site, analyze, geo

TEN_KM_LON = 10_000 / (geo.MEAN_RADIUS_M * 3.141592653589793 / 180)


def flat_link(agl, fake_chain, **opts):
    chain, _ = fake_chain(lambda lat, lon: 0.0)
    a = Site("A", 0.0, 0.0, agl_m=agl)
    b = Site("B", 0.0, TEN_KM_LON, agl_m=agl)
    return analyze(a, b, Options(**opts), elevation=chain)


def test_t10_flat_terrain_clear(fake_chain):
    r = flat_link(10, fake_chain)
    assert r.distance_m == pytest.approx(10_000, rel=0.004)
    assert r.min_clearance == pytest.approx((10 - 1.4715) / 11.3675, abs=0.01)
    assert r.min_clearance == pytest.approx(0.75, abs=0.01)
    assert r.status == "CLEAR"
    assert r.min_clearance_d_m == pytest.approx(r.distance_m / 2, rel=0.01)


def test_t10_flat_terrain_marginal(fake_chain):
    r = flat_link(5, fake_chain)
    assert r.min_clearance == pytest.approx(0.31, abs=0.01)
    assert r.status == "MARGINAL"
    assert r.obstructions


def test_blocked_by_ridge(fake_chain):
    chain, _ = fake_chain(lambda lat, lon: 50.0 if abs(lon - TEN_KM_LON / 2) < 0.001 else 0.0)
    r = analyze(Site("A", 0, 0, agl_m=10), Site("B", 0, TEN_KM_LON, agl_m=10), elevation=chain)
    assert r.status == "BLOCKED" and r.min_clearance < 0


def test_k_factor_and_clutter_reduce_clearance(fake_chain):
    base = flat_link(10, fake_chain)
    worst = flat_link(10, fake_chain, k=2 / 3)
    trees = flat_link(10, fake_chain, clutter_m=5)
    assert worst.min_clearance < base.min_clearance
    assert trees.min_clearance == pytest.approx((10 - 5 - 1.4715) / 11.3675, abs=0.01)


def test_terrain_station_count(fake_chain):
    r = flat_link(10, fake_chain)
    assert len(r.profile) == 65
    chain, _ = fake_chain()
    long = analyze(Site("A", 0, 0, agl_m=100), Site("B", 0, 0.9, agl_m=100), elevation=chain)
    assert len(long.profile) == 402  # ceil(100 km / 250 m) + 1


def test_report_dict_is_json_ready(fake_chain):
    import json
    r = flat_link(10, fake_chain)
    data = json.loads(json.dumps(r.to_dict()))
    assert data["status"] == "CLEAR"
    assert data["sites"][0]["display"] == "0.0000000, 0.0000000"
    assert data["profile"][32]["zone_bottom_m"] == pytest.approx(10 - 11.37, abs=0.05)
    assert data["zone_color"]["random"] is True


def test_coord_display_in_report():
    r = analyze(Site("A", 37.0341667, -122.0833333, asl_m=10), Site("B", 37.05, -121.9, asl_m=10),
                Options(coord_display="dms"))
    assert r.to_dict()["sites"][0]["display"] == "37°02'03.0\"N 122°05'00.0\"W"

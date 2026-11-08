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


def test_close_up_windows_next_to_each_tower(fake_chain):
    r = flat_link(10, fake_chain)
    window = 500 * 0.3048
    assert r.end_window_m == pytest.approx(window)
    for pts, start in ((r.profile_a, 0.0), (r.profile_b, r.distance_m - window)):
        assert len(pts) == 26
        ds = [p.d_m for p in pts]
        assert ds == sorted(ds)
        assert ds[0] == pytest.approx(start, abs=1e-6)
        assert ds[-1] - ds[0] == pytest.approx(window, abs=1e-6)
        for p in pts:
            assert geo.inverse(0.0, 0.0, p.lat, p.lon)[0] == pytest.approx(p.d_m, abs=0.05)
    assert r.profile_b[-1].d_m == pytest.approx(r.distance_m)


def test_close_up_catches_obstacle_between_main_samples(fake_chain):
    # a 30 m wide ridge 100 m from A falls between the main profile's 156 m spacing
    chain, _ = fake_chain(lambda lat, lon: 25.0 if 0.0008 < lon < 0.00108 else 0.0)
    r = analyze(Site("A", 0, 0, agl_m=10), Site("B", 0, TEN_KM_LON, agl_m=10), elevation=chain)
    assert not any(p.ground_m for p in r.profile)
    assert any(p.ground_m for p in r.profile_a)
    assert r.status == "BLOCKED"
    assert r.min_clearance_d_m < 200


@pytest.mark.parametrize("window,samples", [(50.0, 25), (500.0, 83), (5000.0, 200)])
def test_close_up_window_is_configurable(fake_chain, window, samples):
    r = flat_link(10, fake_chain, end_window_m=window)
    assert r.end_window_m == pytest.approx(window)
    assert len(r.profile_a) == len(r.profile_b) == samples + 1  # same ~6 m spacing as the default, up to 200
    assert r.profile_a[-1].d_m == pytest.approx(window, abs=1e-6)


def test_close_up_window_option_parsing_and_limits():
    from fresnelkml.analysis import AnalysisError, validate_options
    from fresnelkml.parsing import parse_options_dict
    opt, _, _ = parse_options_dict({"units": "ft", "end_window_m": 1000})
    assert opt.end_window_m == pytest.approx(304.8)
    assert Options().end_window_m == pytest.approx(152.4)
    for bad in (1.0, 20_000.0):
        with pytest.raises(AnalysisError):
            validate_options(Options(end_window_m=bad))


def test_close_up_window_shrinks_on_short_links(fake_chain):
    chain, _ = fake_chain()
    r = analyze(Site("A", 0, 0, agl_m=10), Site("B", 0, 0.002, agl_m=10), elevation=chain)
    assert r.end_window_m == pytest.approx(r.distance_m / 2)


def test_report_dict_is_json_ready(fake_chain):
    import json
    r = flat_link(10, fake_chain)
    data = json.loads(json.dumps(r.to_dict()))
    assert data["status"] == "CLEAR"
    assert data["sites"][0]["display"] == "0.0000000, 0.0000000"
    assert data["profile"][32]["zone_bottom_m"] == pytest.approx(10 - 11.37, abs=0.05)
    assert data["profile"][32]["bulge_m"] == pytest.approx(1.47, abs=0.02)
    assert data["profile"][0]["bulge_m"] == data["profile"][-1]["bulge_m"] == 0
    assert data["zone_color"]["random"] is True
    assert len(data["profile_a"]) == len(data["profile_b"]) == 26
    assert data["end_window_m"] == pytest.approx(152.4)


def test_coord_display_in_report():
    r = analyze(Site("A", 37.0341667, -122.0833333, asl_m=10), Site("B", 37.05, -121.9, asl_m=10),
                Options(coord_display="dms"))
    assert r.to_dict()["sites"][0]["display"] == "37°02'03.0\"N 122°05'00.0\"W"

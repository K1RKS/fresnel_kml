import json
import xml.etree.ElementTree as ET

from fresnelkml.cli import main


def test_asl_sites_write_kml_and_report(tmp_path, capsys):
    out = tmp_path / "link.kml"
    report = tmp_path / "report.json"
    code = main(["--a", "TowerA,37.0,-122.0,150asl", "--b", "TowerB,10S 597811 4100985,180asl",
                 "-o", str(out), "--json-report", str(report), "--color", "blue"], env={})
    assert code == 0
    ET.parse(out)
    data = json.loads(report.read_text())
    assert data["zone_color"]["hex"] == "#0000ff"
    assert data["sites"][1]["from"] == "utm"
    text = capsys.readouterr().out
    assert "Distance" in text and "Terrain check: not run" in text


def test_hgt_dir_chain(tmp_path, tile_dir, capsys):
    out = tmp_path / "link.kml"
    code = main(["--a", "A,37.0,-122.0,30", "--b", "B,37.05,-121.9,25", "--hgt-dir", tile_dir, "-o", str(out)],
                env={})
    assert code == 0
    assert "Terrain check:" in capsys.readouterr().out


def test_stdout_output(capsys):
    code = main(["--a", "A,37.0,-122.0,150asl", "--b", "B,37.05,-121.9,180asl", "-o", "-"], env={})
    captured = capsys.readouterr()
    assert code == 0 and captured.out.startswith("<?xml") and "Distance" in captured.err


def test_input_error_exit_code(capsys):
    code = main(["--a", "A,95,-122.0,30asl", "--b", "B,37.05,-121.9,25asl"], env={})
    assert code == 2
    assert "out of range" in capsys.readouterr().err


def test_no_provider_exit_code(capsys):
    code = main(["--a", "A,37.0,-122.0,30", "--b", "B,37.05,-121.9,25", "--elev-provider", "none"], env={})
    assert code == 3
    assert "--ground-a" in capsys.readouterr().err


def test_feet_and_ground_overrides(tmp_path):
    out = tmp_path / "x.kml"
    report = tmp_path / "r.json"
    code = main(["--a", "A,37.0,-122.0,100", "--b", "B,37.05,-121.9,100", "--ground-a", "1000",
                 "--ground-b", "1000", "--units", "ft", "-o", str(out), "--json-report", str(report), "-q"], env={})
    assert code == 0
    site = json.loads(report.read_text())["sites"][0]
    assert site["asl_m"] == round(1100 * 0.3048, 2)

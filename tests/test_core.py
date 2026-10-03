import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from splitroll import imaging, jobs                     # noqa: E402
from splitroll.library import Library, migrate_v1      # noqa: E402
import make_samples                                     # noqa: E402


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("samples")
    make_samples.main(d)
    return d


def wait(job):
    for _ in range(600):
        j = jobs.get_job(job["id"])
        if not j["running"]:
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_load_16bit_keeps_depth_and_icc(samples):
    p = sorted((samples / "roll16").glob("*.tif"))[0]
    pic = imaging.load(str(p))
    assert pic.arr.dtype == np.uint16 and pic.arr.shape == (1200, 1810, 3)
    assert pic.icc and pic.dpi == (2400.0, 2400.0)
    assert imaging.probe(str(p)) == (1810, 1200, 16)


def test_detect_finds_gap(samples):
    for i, p in enumerate(sorted((samples / "roll16").glob("*.tif"))):
        pic = imaging.load(str(p))
        det = imaging.detect_cut(imaging.to_pil(pic.arr), pic.size[0])
        expect = (0.5 + (i - 3) * 0.008) * 100
        assert det, p
        assert abs(det[0] - expect) < 0.4, (det, expect)
        assert 25 <= det[1] <= 48


def test_detect_rejects_noise(samples):
    p = samples / "rolljpg" / "jpg" / "26090226090009.jpg"
    pic = imaging.load(str(p))
    assert imaging.detect_cut(imaging.to_pil(pic.arr), pic.size[0]) is None


def test_split_bounds():
    assert imaging.split_bounds(1000, 50, 0) == (500, 500)
    assert imaging.split_bounds(1000, 50, 40) == (480, 520)
    assert imaging.split_bounds(1000, 50, 41) == (480, 521)
    assert imaging.split_bounds(1000, 0, 0) == (50, 50)          # clamped to 5%
    assert imaging.split_bounds(1000, 100, 10) == (945, 955)


@pytest.mark.parametrize("fmt", ["tiff", "png", "jpeg"])
def test_save_roundtrip(tmp_path, fmt):
    a = (np.random.default_rng(1).random((40, 30, 3)) * 65535).astype(np.uint16)
    out = tmp_path / f"x.{jobs.EXT[fmt]}"
    imaging.save(a, out, fmt, icc=b"fakeicc" * 20, dpi=(300, 300))
    b = imaging.load(str(out))
    if fmt == "jpeg":
        assert b.arr.dtype == np.uint8
    else:
        assert b.arr.dtype == np.uint16
        assert np.array_equal(a, b.arr)            # bit-exact lossless
    assert b.icc == b"fakeicc" * 20
    assert not out.with_name(out.name + ".part").exists()


def test_png16_readable_by_pillow(tmp_path):
    a = (np.arange(64 * 48 * 3) % 65535).astype(np.uint16).reshape(48, 64, 3)
    imaging.save(a, tmp_path / "x.png", "png", dpi=(300, 300))
    with Image.open(tmp_path / "x.png") as im:
        assert im.size == (64, 48)
        assert round(im.info["dpi"][0]) == 300


def test_migrate_v1():
    old = {"rolls": [{"id": "a", "name": "R", "film": "F", "date": "2026-01-01", "deadZone": 140,
                      "fullFrame": False,
                      "frames": [{"path": "/x/1.tif", "name": "1.tif", "w": 10, "h": 5},
                                 {"path": "/x/2.tif", "name": "2.tif", "w": 10, "h": 5},
                                 {"path": "/x/3.tif", "name": "3.tif", "w": 10, "h": 5}],
                      "crops": {"0": {"pct": 48, "lRot": 90, "rRot": 0, "skip": False},
                                "1": {"pct": 50, "skip": True}},
                      "rots": {"0": {"l": 90, "r": 0}}, "inputRots": {"2": 90},
                      "pos": {"2": 11.5}, "expSel": [0]}]}
    r = migrate_v1(old)[0]
    f0, f1, f2 = r["frames"]
    assert r["border"] == 140
    assert (f0["state"], f0["cut"], f0["lRot"], f0["export"]) == ("split", 48, 90, True)
    assert (f1["state"], f1["export"]) == ("full", False)
    assert (f2["state"], f2["rot"], f2["cut"]) == ("pending", 90, 11.5)


def test_real_v1_library_migrates():
    p = ROOT / "tests" / "halfframe_data.json"
    if not p.exists():
        pytest.skip("no sample library")
    rolls = migrate_v1(json.loads(p.read_text()))
    assert [len(r["frames"]) for r in rolls] == [37, 37, 38]
    assert rolls[0]["border"] == 140
    assert sum(f["state"] != "pending" for f in rolls[1]["frames"]) == 36


def test_import_export_end_to_end(samples, tmp_path):
    lib = Library(tmp_path / "lib.json")
    cache = jobs.Cache(tmp_path / "cache")
    r = lib.add_roll("Pentax17 #004: test/roll")
    j = wait(jobs.import_folder(lib, cache, r["id"], samples / "roll16"))
    assert j["failed"] == 0 and len(r["frames"]) == 6
    assert r["border"] >= 30                                   # auto border from detected gaps
    assert all(f["auto"] and f["bits"] == 16 for f in r["frames"])
    # re-import is idempotent
    wait(jobs.import_folder(lib, cache, r["id"], samples / "roll16"))
    assert len(r["frames"]) == 6
    # subfolder discovery
    r2 = lib.add_roll("jpg")
    wait(jobs.import_folder(lib, cache, r2["id"], samples / "rolljpg"))
    assert len(r2["frames"]) == 9
    # mark & export
    f = r["frames"]
    lib.update_frames(r["id"], [{"id": f[0]["id"], "state": "split", "lRot": 90},
                                {"id": f[1]["id"], "state": "full", "rot": 90},
                                {"id": f[2]["id"], "state": "split", "export": False}])
    out = tmp_path / "out"
    plan = jobs.plan_export(r, None, "tiff", "roll")
    names = [n for *_, n in plan]
    assert names[0].startswith("Pentax17 #004_ test_roll_01L")    # sanitised
    j = wait(jobs.export(lib, r["id"], [x["id"] for x in f if x["export"]], out, "tiff", "roll", "overwrite"))
    assert j["failed"] == 0, j["errors"]
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 3, files
    L = tifffile.imread(out / names[0])
    full = tifffile.imread(out / names[2])
    assert L.dtype == np.uint16
    le, rs = imaging.split_bounds(1810, f[0]["cut"], r["border"])
    assert L.shape == (le, 1200, 3)                                 # rotated 90°
    assert full.shape == (1810, 1200, 3)
    # gap must be gone: no near-black columns in the right half's first columns
    R = tifffile.imread(out / names[1])
    assert R[:, :3].mean() > 3000
    # conflicts + skip
    assert len(jobs.existing_conflicts(plan, out)) == 3
    j = wait(jobs.export(lib, r["id"], [f[0]["id"]], out, "tiff", "roll", "skip"))
    assert "skipped" in j["msg"]
    # contact sheet
    j = wait(jobs.contact_sheet(lib, cache, r["id"], out))
    assert Path(j["result"]["path"]).exists()
    lib.flush()
    lib2 = Library(tmp_path / "lib.json")
    assert len(lib2.rolls) == 2 and lib2.rolls[0]["frames"][0]["state"] == "split"


def test_relink(samples, tmp_path):
    lib = Library(tmp_path / "lib.json")
    cache = jobs.Cache(tmp_path / "cache")
    r = lib.add_roll("x")
    wait(jobs.import_folder(lib, cache, r["id"], samples / "roll16"))
    for fr in r["frames"]:
        fr["path"] = "/gone/" + fr["name"]
    n, miss = jobs.relink(lib, r["id"], samples)
    assert (n, miss) == (6, 0)

"""--plugin / --test-cell: OBND from the real NIF, ModForge spec, black-box build, publish rules."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys

import pytest

from any2nif.errors import AnyError
from any2nif.package import MANIFEST
from any2nif.plugin import (FLOOR_STATIC, FLOOR_TILE, build_spec, editor_id, object_bounds,
                            _tile_offsets)
from tests.test_any2nif_package import _source

ROOT = Path(__file__).resolve().parents[1]
SKYRIM_DATA = Path(os.environ.get(
    "MODFORGE_SKYRIM_DATA",
    Path.home() / ".local/share/Steam/steamapps/common/Skyrim Special Edition/Data"))
MODFORGE_DLL = (Path(os.environ.get("MODFORGE_DIR") or ROOT.parent / "ModForge")
                / "src/ModForge.Cli/bin/Debug/net10.0/ModForge.Cli.dll")

FAKE_MODFORGE = r'''
import json, sys, pathlib
mode = sys.argv[1]
_, spec, out = sys.argv[2:5]
log = pathlib.Path(sys.argv[0]).with_suffix(".log")
log.write_text(json.dumps({"argv": sys.argv[2:], "spec": json.loads(pathlib.Path(spec).read_text())}))
if mode == "fail":
    print("boom: spec rejected", file=sys.stderr); sys.exit(3)
pathlib.Path(out).write_bytes(b"TES4" + b"\0" * 20)
if mode == "sidecar":
    pathlib.Path(out).with_suffix(".requires.txt").write_text("Foo.esm\n")
'''


def _fake(tmp_path, mode="ok"):
    script = tmp_path / f"fake_modforge_{mode}.py"
    script.write_text(FAKE_MODFORGE)
    return script, f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {mode}"


def _run(args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "any2nif", *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, env=env)


def _snapshot(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


# ---- pure helpers ---------------------------------------------------------------------

def test_editor_id_is_ascii_camel_with_prefix():
    assert editor_id("sheen_chair-v2") == "A2N_SheenChairV2"
    assert editor_id("crate") == "A2N_Crate"


def test_spec_strips_meshes_prefix_and_uses_backslashes():
    spec = build_spec("crate", "meshes/any2nif/crate/crate.nif", (0, -71, 0), (71, 0, 71),
                      test_cell=False)
    assert spec["esl"] is True and spec["pluginName"] == "any2nif_crate.esp"
    assert spec["statics"] == [{"editorId": "A2N_Crate", "model": "any2nif\\crate\\crate.nif",
                                "objectBoundsMin": {"x": 0, "y": -71, "z": 0},
                                "objectBoundsMax": {"x": 71, "y": 0, "z": 71}}]
    assert "cells" not in spec and "placements" not in spec
    with pytest.raises(AnyError):
        build_spec("crate", "textures/x.nif", (0, 0, 0), (1, 1, 1), test_cell=False)


def test_test_cell_floor_covers_model_and_model_rests_on_floor():
    low, high = (-1700, -300, -40), (900, 2600, 500)
    spec = build_spec("big", "meshes/any2nif/big/big.nif", low, high, test_cell=True)
    cell = spec["cells"][0]["editorId"]
    floors = [p["position"] for p in spec["placements"] if p["base"] == FLOOR_STATIC]
    assert all(p["z"] == 0 for p in floors)
    for axis, lo, hi in (("x", low[0], high[0]), ("y", low[1], high[1])):
        covered_lo = min(p[axis] for p in floors) - FLOOR_TILE / 2
        covered_hi = max(p[axis] for p in floors) + FLOOR_TILE / 2
        assert covered_lo <= lo - FLOOR_TILE and covered_hi >= hi + FLOOR_TILE
    model = [p for p in spec["placements"] if p["base"] == "A2N_Big"]
    assert model == [{"base": "A2N_Big", "cell": cell, "position": {"x": 0, "y": 0, "z": 40}}]
    marker = spec["placements"][-1]["position"]
    assert marker["y"] < low[1]  # player starts outside the model's footprint


def test_test_cell_refuses_absurd_floor():
    with pytest.raises(AnyError, match="too large"):
        _tile_offsets((-20000, 0, 0), (0, 10, 10))


def test_object_bounds_read_from_written_nif_and_reject_int16_overflow(tmp_path):
    source = _source(tmp_path, material_name="crate")
    out = tmp_path / "pkg"
    assert _run([source, out, "--package", "--scale", "2"]).returncode == 0
    nif = (out / "meshes/any2nif/crate/crate.nif").read_bytes()
    low, high = object_bounds(nif)
    # Unit cube 0..1 m, x2 scale, glTF Y-up -> Skyrim Z-up (y_sky = -z_gltf), 70.03 units/m.
    assert low == (0, -141, 0) and high == (141, 0, 141)
    big = tmp_path / "huge"
    assert _run([source, big, "--package", "--scale", "500", "--collision", "none"]).returncode == 0
    with pytest.raises(AnyError, match="int16"):
        object_bounds((big / "meshes/any2nif/crate/crate.nif").read_bytes())


# ---- CLI with a fake ModForge -------------------------------------------------------

def test_plugin_package_lists_esp_and_spec_in_manifest(tmp_path):
    source = _source(tmp_path, material_name="crate")
    script, command = _fake(tmp_path)
    out = tmp_path / "Crate"
    result = _run([source, out, "--package", "--plugin", "--test-cell"], {"MODFORGE_CLI": command})
    assert result.returncode == 0, result.stderr
    assert "coc A2N_Crate_TestCell" in result.stderr
    manifest = json.loads((out / MANIFEST).read_text())
    plugin = manifest["plugin"]
    assert plugin == {"file": "any2nif_crate.esp", "spec": "modforge-spec.json",
                      "editor_id": "A2N_Crate", "object_bounds": [[0, -71, 0], [71, 0, 71]],
                      "test_cell": "A2N_Crate_TestCell"}
    for name in ("any2nif_crate.esp", "modforge-spec.json"):
        assert manifest["sha256"][name] == hashlib.sha256((out / name).read_bytes()).hexdigest()
    call = json.loads(script.with_suffix(".log").read_text())
    assert call["argv"][0] == "build" and call["argv"][2].endswith("any2nif_crate.esp")
    assert call["spec"] == json.loads((out / "modforge-spec.json").read_text())
    # Rerun over an untouched plugin package is allowed (manifest covers the new files).
    again = _run([source, out, "--package", "--plugin"], {"MODFORGE_CLI": command})
    assert again.returncode == 0, again.stderr
    assert "test_cell" not in json.loads((out / MANIFEST).read_text())["plugin"]


def test_plugin_package_still_without_plugin_by_default(tmp_path):
    source = _source(tmp_path, material_name="crate")
    out = tmp_path / "Crate"
    assert _run([source, out, "--package"]).returncode == 0
    assert "plugin" not in json.loads((out / MANIFEST).read_text())
    assert not list(out.glob("*.esp"))


@pytest.mark.parametrize("mode, message", [("fail", "boom: spec rejected"),
                                           ("sidecar", "unexpected ModForge output")])
def test_modforge_failure_keeps_previous_package(tmp_path, mode, message):
    source = _source(tmp_path, material_name="crate")
    out = tmp_path / "Crate"
    assert _run([source, out, "--package"]).returncode == 0
    before = _snapshot(out)
    _, command = _fake(tmp_path, mode)
    result = _run([source, out, "--package", "--plugin"], {"MODFORGE_CLI": command})
    assert result.returncode != 0 and message in result.stderr
    assert _snapshot(out) == before


def test_missing_modforge_fails_before_conversion(tmp_path):
    source = _source(tmp_path, material_name="crate")
    out = tmp_path / "Crate"
    env = {"MODFORGE_CLI": "", "MODFORGE_DIR": str(tmp_path / "no-modforge")}
    result = _run([source, out, "--package", "--plugin"], env)
    assert result.returncode == 1 and "ModForge CLI not found" in result.stderr
    assert not out.exists()


def test_flag_dependencies(tmp_path):
    source = _source(tmp_path, material_name="crate")
    result = _run([source, tmp_path / "x.nif", "--plugin"])
    assert result.returncode == 1 and "require --package" in result.stderr
    result = _run([source, tmp_path / "pkg", "--package", "--test-cell"])
    assert result.returncode == 1 and "--test-cell requires --plugin" in result.stderr
    assert not (tmp_path / "pkg").exists()


# ---- real ModForge, read back with an independent TES4 parser ----------------------

def _records(data):
    """Yield (type, formid, flags, payload) for every record, descending into GRUPs."""
    def walk(start, end):
        pos = start
        while pos < end:
            kind = data[pos:pos + 4].decode("ascii")
            size = struct.unpack_from("<I", data, pos + 4)[0]
            if kind == "GRUP":
                yield from walk(pos + 24, pos + size)
                pos += size
            else:
                flags, formid = struct.unpack_from("<II", data, pos + 8)
                yield kind, formid, flags, data[pos + 24:pos + 24 + size]
                pos += 24 + size
    yield from walk(0, len(data))


def _subrecords(payload):
    fields, pos = {}, 0
    while pos < len(payload):
        kind = payload[pos:pos + 4].decode("ascii")
        size = struct.unpack_from("<H", payload, pos + 4)[0]
        fields.setdefault(kind, []).append(payload[pos + 6:pos + 6 + size])
        pos += 6 + size
    return fields


@pytest.mark.skipif(not (shutil.which("dotnet") and MODFORGE_DLL.is_file()
                         and (SKYRIM_DATA / "Skyrim.esm").is_file()),
                    reason="needs built sibling ModForge CLI, dotnet and Skyrim.esm")
def test_real_modforge_writes_esl_stat_with_nif_bounds(tmp_path):
    source = _source(tmp_path, material_name="crate")
    out = tmp_path / "Crate"
    result = _run([source, out, "--package", "--plugin", "--test-cell"],
                  {"MODFORGE_CLI": "", "MODFORGE_DIR": str(MODFORGE_DLL.parents[5])})
    assert result.returncode == 0, result.stderr
    data = (out / "any2nif_crate.esp").read_bytes()
    records = list(_records(data))
    tes4 = records[0]
    assert tes4[0] == "TES4" and tes4[2] & 0x200  # ESL flag
    header = _subrecords(tes4[3])
    assert [m.rstrip(b"\0") for m in header["MAST"]] == [b"Skyrim.esm"]
    stats = [r for r in records if r[0] == "STAT"]
    assert len(stats) == 1
    fields = _subrecords(stats[0][3])
    assert fields["EDID"][0].rstrip(b"\0") == b"A2N_Crate"
    assert fields["MODL"][0].rstrip(b"\0") == b"any2nif\\crate\\crate.nif"
    assert struct.unpack("<6h", fields["OBND"][0]) == (0, -71, 0, 71, 0, 71)
    nif = out / "meshes" / Path(fields["MODL"][0].rstrip(b"\0").decode().replace("\\", "/"))
    assert nif.is_file()
    assert object_bounds(nif.read_bytes()) == ((0, -71, 0), (71, 0, 71))
    cells = [r for r in records if r[0] == "CELL"]
    assert len(cells) == 1
    assert _subrecords(cells[0][3])["EDID"][0].rstrip(b"\0") == b"A2N_Crate_TestCell"
    refs = [_subrecords(r[3]) for r in records if r[0] == "REFR"]
    bases = [struct.unpack("<I", f["NAME"][0])[0] for f in refs]
    stat_id = stats[0][1]
    assert bases.count(stat_id) == 1
    assert bases.count(0x1044AA) == 9 and bases.count(0x32) == 1

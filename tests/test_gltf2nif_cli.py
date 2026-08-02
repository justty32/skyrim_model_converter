"""gltf2nif CLI contract: real .gltf -> .nif, exit codes, collision flag."""

from __future__ import annotations

import json

from gltf2nif.cli import main
from nif2gltf.nif_reader import read_nif
from tests.gltf2nif_fixtures import write_gltf_interleaved

_PRIM = [{
    "positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
    "normals": [(0, 0, 1)] * 3,
    "uvs": [(0, 0), (1, 0), (0, 1)],
    "triangles": [(0, 1, 2)],
    "material": "wall",
}]


def test_convert_ok(tmp_path):
    src = str(tmp_path / "in.gltf")
    out = str(tmp_path / "out.nif")
    write_gltf_interleaved(src, _PRIM)
    assert main([src, out, "--texprefix", "textures\\x"]) == 0
    meshes = read_nif(open(out, "rb").read())
    assert len(meshes) == 1
    assert meshes[0].texture == "textures\\x\\wall.dds"


def test_missing_input_exit1(tmp_path):
    assert main([str(tmp_path / "nope.gltf"), str(tmp_path / "o.nif")]) == 1


def test_bad_gltf_exit2(tmp_path):
    bad = tmp_path / "bad.gltf"
    bad.write_text("{ not valid gltf")
    assert main([str(bad), str(tmp_path / "o.nif")]) == 2


def test_collision_flag(tmp_path):
    src = str(tmp_path / "in.gltf")
    out = str(tmp_path / "out.nif")
    write_gltf_interleaved(src, _PRIM)
    hulls = tmp_path / "h.json"
    hulls.write_text(json.dumps({"hulls": [
        {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]}]}))
    assert main([src, out, "--collision", str(hulls)]) == 0
    from nif2gltf.nif_reader import _read_header
    from nif2gltf._binreader import _Reader
    h = _read_header(_Reader(open(out, "rb").read()))
    assert "bhkConvexVerticesShape" in h["types"]


def test_bad_collision_exit1(tmp_path):
    src = str(tmp_path / "in.gltf")
    write_gltf_interleaved(src, _PRIM)
    bad = tmp_path / "h.json"
    bad.write_text(json.dumps({"hulls": [{"vertices": [[0, 0, 0], [1, 0, 0]]}]}))  # <4 verts
    assert main([src, str(tmp_path / "o.nif"), "--collision", str(bad)]) == 1

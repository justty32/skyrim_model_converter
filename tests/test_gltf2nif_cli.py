"""gltf2nif CLI contract: real .gltf -> .nif, exit codes, collision flag."""

from __future__ import annotations

import json
import struct

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


def test_material_overrides_merge_skip_and_write_exact_slots(tmp_path):
    src = str(tmp_path / "in.gltf")
    out = str(tmp_path / "out.nif")
    write_gltf_interleaved(src, _PRIM * 3)
    overrides = tmp_path / "materials.json"
    overrides.write_text(json.dumps({
        "version": 1,
        "materials": [
            {
                "mesh": "mesh0", "group": "foliage", "double_sided": True,
                "diffuse_texture_name": r"textures\x\leaf.dds",
                "normal_texture_name": r"textures\x\leaf_n.dds",
                "alpha_mode": "MASK", "alpha_flags_override": 4844,
                "alpha_threshold_override": 128,
            },
            {
                "mesh": "mesh1", "group": "foliage", "double_sided": True,
                "diffuse_texture_name": r"textures\x\leaf.dds",
                "normal_texture_name": r"textures\x\leaf_n.dds",
                "alpha_mode": "MASK", "alpha_flags_override": 4844,
                "alpha_threshold_override": 128,
            },
            {"mesh": "mesh2", "group": "water", "skip": True},
        ],
    }))
    assert main([src, out, "--materials", str(overrides)]) == 0

    data = open(out, "rb").read()
    meshes = read_nif(data)
    assert len(meshes) == 1
    assert len(meshes[0].triangles) == 2
    assert meshes[0].texture == r"textures\x\leaf.dds"
    from nif2gltf._binreader import _Reader
    from nif2gltf._blocks import _read_bsshadertextureset
    from nif2gltf.nif_reader import _read_header
    header = _read_header(_Reader(data))
    assert header["types"].count("NiAlphaProperty") == 1
    lsp = header["types"].index("BSLightingShaderProperty")
    flags2 = struct.unpack_from("<I", data, header["offsets"][lsp] + 20)[0]
    assert flags2 & 0x10
    texset = header["types"].index("BSShaderTextureSet")
    reader = _Reader(data)
    reader.seek(header["offsets"][texset])
    assert _read_bsshadertextureset(reader)["textures"][:2] == [
        r"textures\x\leaf.dds", r"textures\x\leaf_n.dds"]


def test_material_overrides_fail_closed_on_mesh_mismatch(tmp_path):
    src = str(tmp_path / "in.gltf")
    write_gltf_interleaved(src, _PRIM)
    overrides = tmp_path / "materials.json"
    overrides.write_text(json.dumps({
        "version": 1, "materials": [{"mesh": "wrong-name"}],
    }))
    assert main([src, str(tmp_path / "out.nif"),
                 "--materials", str(overrides)]) == 1

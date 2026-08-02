"""gltf2nif reader: parse real (synthetic) interleaved glTF into Mesh IR."""

from __future__ import annotations

import pytest

from gltf2nif._binwriter import GltfError
from gltf2nif.gltf_reader import probe_normal_map, read_gltf
from tests.gltf2nif_fixtures import CUBE_POS, CUBE_TRIS, write_gltf_interleaved


def test_read_interleaved_single(tmp_path):
    p = str(tmp_path / "one.gltf")
    write_gltf_interleaved(p, [{
        "positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        "normals": [(0, 0, 1)] * 3,
        "uvs": [(0, 0), (1, 0), (0, 1)],
        "triangles": [(0, 1, 2)],
        "material": "m18_wall_07.tga",
    }])
    meshes = read_gltf(p)
    assert len(meshes) == 1
    m = meshes[0]
    assert len(m.positions) == 3
    assert m.triangles == [(0, 1, 2)]
    assert m.has_normals and m.has_uvs
    assert m.material == "m18_wall_07"  # .tga extension stripped
    assert m.positions[1] == pytest.approx((1.0, 0.0, 0.0))
    assert m.uvs[2] == pytest.approx((0.0, 1.0))


def test_read_multi_primitive(tmp_path):
    p = str(tmp_path / "two.gltf")
    write_gltf_interleaved(p, [
        {"positions": [tuple(map(float, q)) for q in CUBE_POS],
         "normals": [(0, 0, 1)] * 8, "uvs": [(0, 0)] * 8,
         "triangles": CUBE_TRIS, "material": "a"},
        {"positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
         "normals": [(0, 0, 1)] * 3, "uvs": [(0, 0), (1, 0), (0, 1)],
         "triangles": [(0, 1, 2)], "material": "b"},
    ])
    meshes = read_gltf(p)
    assert len(meshes) == 2
    assert {m.material for m in meshes} == {"a", "b"}
    assert len(meshes[0].triangles) == 12


def test_read_empty_raises(tmp_path):
    p = str(tmp_path / "empty.gltf")
    from pygltflib import GLTF2
    GLTF2().save_json(p)
    with pytest.raises(GltfError):
        read_gltf(p)


def test_probe_normal_map(tmp_path):
    (tmp_path / "wall_n.dds").write_bytes(b"\x00")
    assert probe_normal_map(str(tmp_path), "wall") is True
    assert probe_normal_map(str(tmp_path), "other") is False
    assert probe_normal_map(str(tmp_path), "") is False

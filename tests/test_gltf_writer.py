"""glTF writer round-trip — the offline-certain half (no NIF needed)."""

from __future__ import annotations

import os

import numpy as np
import pytest
from pygltflib import GLTF2

from nif2gltf.geometry import Mesh
from nif2gltf.gltf_writer import write_gltf


def _unit_triangle() -> Mesh:
    return Mesh(
        name="tri",
        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        normals=[(0.0, 0.0, 1.0)] * 3,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        triangles=[(0, 1, 2)],
    )


def _read_accessor(gltf: GLTF2, bin_dir: str, accessor_index: int) -> np.ndarray:
    acc = gltf.accessors[accessor_index]
    bv = gltf.bufferViews[acc.bufferView]
    buf = gltf.buffers[bv.buffer]
    with open(os.path.join(bin_dir, buf.uri), "rb") as fh:
        raw = fh.read()
    comp = {5126: np.float32, 5125: np.uint32, 5123: np.uint16}[acc.componentType]
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[acc.type]
    start = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    data = np.frombuffer(raw, dtype=comp, count=acc.count * ncomp, offset=start)
    return data.reshape(acc.count, ncomp) if ncomp > 1 else data


def test_writes_gltf_and_bin(tmp_path):
    out = str(tmp_path / "tri.gltf")
    write_gltf([_unit_triangle()], out)
    assert os.path.exists(out)
    assert os.path.exists(str(tmp_path / "tri.bin"))


def test_roundtrip_positions_normals_uvs(tmp_path):
    out = str(tmp_path / "tri.gltf")
    write_gltf([_unit_triangle()], out)

    gltf = GLTF2().load(out)
    assert len(gltf.meshes) == 1
    prim = gltf.meshes[0].primitives[0]
    assert prim.material == 0

    pos = _read_accessor(gltf, str(tmp_path), prim.attributes.POSITION)
    nrm = _read_accessor(gltf, str(tmp_path), prim.attributes.NORMAL)
    uv = _read_accessor(gltf, str(tmp_path), prim.attributes.TEXCOORD_0)
    idx = _read_accessor(gltf, str(tmp_path), prim.indices)

    np.testing.assert_allclose(pos, [(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    np.testing.assert_allclose(nrm, [(0, 0, 1)] * 3)
    np.testing.assert_allclose(uv, [(0, 0), (1, 0), (0, 1)])
    np.testing.assert_array_equal(idx, [0, 1, 2])


def test_position_accessor_has_min_max(tmp_path):
    out = str(tmp_path / "tri.gltf")
    write_gltf([_unit_triangle()], out)
    gltf = GLTF2().load(out)
    pos_acc = gltf.accessors[gltf.meshes[0].primitives[0].attributes.POSITION]
    assert pos_acc.min == [0.0, 0.0, 0.0]
    assert pos_acc.max == [1.0, 1.0, 0.0]


def test_flat_material_has_no_textures(tmp_path):
    out = str(tmp_path / "tri.gltf")
    write_gltf([_unit_triangle()], out)
    gltf = GLTF2().load(out)
    assert len(gltf.materials) == 1
    assert not gltf.textures
    assert not gltf.images


def test_mesh_without_normals_or_uvs(tmp_path):
    m = Mesh(
        name="bare",
        positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        triangles=[(0, 1, 2)],
    )
    out = str(tmp_path / "bare.gltf")
    write_gltf([m], out)
    gltf = GLTF2().load(out)
    prim = gltf.meshes[0].primitives[0]
    assert prim.attributes.POSITION is not None
    assert prim.attributes.NORMAL is None
    assert prim.attributes.TEXCOORD_0 is None


def test_empty_meshes_are_skipped(tmp_path):
    empty = Mesh(name="empty")
    out = str(tmp_path / "multi.gltf")
    write_gltf([empty, _unit_triangle()], out)
    gltf = GLTF2().load(out)
    assert len(gltf.meshes) == 1


def test_multiple_meshes_share_buffer(tmp_path):
    out = str(tmp_path / "two.gltf")
    write_gltf([_unit_triangle(), _unit_triangle()], out)
    gltf = GLTF2().load(out)
    assert len(gltf.meshes) == 2
    assert len(gltf.nodes) == 2
    assert len(gltf.buffers) == 1

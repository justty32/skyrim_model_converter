"""NIF reader round-trip against synthetic fixtures (offline self-consistency)."""

from __future__ import annotations

import pytest

from nif2gltf.geometry import skyrim_to_gltf_point
from nif2gltf.nif_reader import NifError, SkinnedNifError, read_nif
from tests.nif_fixtures import build_le_nif, build_sse_nif, build_sse_nif_fullprec

TRI = [(0, 1, 2)]
VERTS = [(1.0, 2.0, 3.0), (4.0, 0.0, 0.0), (0.0, 5.0, 0.0)]
NORMALS = [(0.0, 0.0, 1.0)] * 3
UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]


def test_le_single_triangle_axis_swap():
    data = build_le_nif(VERTS, NORMALS, UVS, TRI)
    meshes = read_nif(data)
    assert len(meshes) == 1
    m = meshes[0]
    assert m.triangles == [(0, 1, 2)]
    # No transform -> just Skyrim Z-up -> glTF Y-up.
    for got, src in zip(m.positions, VERTS):
        assert got == pytest.approx(skyrim_to_gltf_point(*src))


def test_le_node_and_shape_translation_compose():
    data = build_le_nif(VERTS, NORMALS, UVS, TRI,
                        node_translation=(10.0, 0.0, 0.0),
                        shape_translation=(0.0, 20.0, 0.0))
    m = read_nif(data)[0]
    # world = node_t + shape_t + vert (identity rot, scale 1), then axis swap.
    for got, (x, y, z) in zip(m.positions, VERTS):
        wx, wy, wz = x + 10.0, y + 20.0, z
        assert got == pytest.approx(skyrim_to_gltf_point(wx, wy, wz))


def test_le_uvs_preserved():
    m = read_nif(build_le_nif(VERTS, NORMALS, UVS, TRI))[0]
    assert m.has_uvs
    for got, src in zip(m.uvs, UVS):
        assert got == pytest.approx(src)


def test_le_without_normals_or_uvs():
    m = read_nif(build_le_nif(VERTS, [], [], TRI))[0]
    assert not m.has_normals
    assert not m.has_uvs
    assert len(m.positions) == 3


def test_sse_half_precision_roundtrip():
    # Half-representable coordinates to avoid precision loss on positions.
    verts = [(1.0, 2.0, 3.0), (4.0, 0.5, 0.0), (0.0, 5.0, 2.5)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
    m = read_nif(build_sse_nif(verts, NORMALS, uvs, TRI))[0]
    assert m.triangles == [(0, 1, 2)]
    for got, src in zip(m.positions, verts):
        assert got == pytest.approx(skyrim_to_gltf_point(*src), abs=1e-3)
    for got, src in zip(m.uvs, uvs):
        assert got == pytest.approx(src, abs=1e-3)


def test_sse_full_precision_unflagged():
    # Real vanilla statics (e.g. RockL01) store float3 positions but DON'T set the Full_Precision
    # attribute flag. Precision must be inferred from the vertex layout (UV offset >= 12 = float3),
    # not the flag — otherwise positions get misread as half3 → garbage / NaN.
    verts = [(132.84, -129.97, 113.68), (51.97, 8e-5, -3.44), (-2.58, -27.02, -3.46)]
    uvs = [(0.25, 0.5), (0.75, 0.0), (0.5, 1.0)]
    m = read_nif(build_sse_nif_fullprec(verts, NORMALS, uvs, TRI))[0]
    for got, src in zip(m.positions, verts):
        # float3 is exact (no half rounding); axis-swapped to glTF space.
        assert got == pytest.approx(skyrim_to_gltf_point(*src), abs=1e-2)


def test_sse_normals_decoded():
    m = read_nif(build_sse_nif(VERTS, NORMALS, UVS, TRI))[0]
    assert m.has_normals
    # (0,0,1) Skyrim normal -> glTF (0,1,0) after axis swap; normbyte rounding tolerance.
    for got in m.normals:
        assert got == pytest.approx((0.0, 1.0, 0.0), abs=0.02)


def test_sse_transform_compose():
    m = read_nif(build_sse_nif(VERTS, NORMALS, UVS, TRI,
                               node_translation=(0.0, 0.0, 8.0)))[0]
    for got, (x, y, z) in zip(m.positions, VERTS):
        assert got == pytest.approx(skyrim_to_gltf_point(x, y, z + 8.0), abs=1e-3)


def test_wrong_version_raises():
    data = bytearray(build_le_nif(VERTS, NORMALS, UVS, TRI))
    # Header string ends with \n at index 38; version u32 follows. Corrupt it.
    nl = data.index(0x0A)
    data[nl + 1:nl + 5] = (0x14010003).to_bytes(4, "little")
    with pytest.raises(NifError):
        read_nif(bytes(data))


def test_truncated_raises():
    data = build_le_nif(VERTS, NORMALS, UVS, TRI)
    with pytest.raises(NifError):
        read_nif(data[:20])

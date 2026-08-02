"""gltf2nif writer: the primary gate — write a NIF, read it back with nif2gltf."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from gltf2nif.collision import Hull
from gltf2nif.geometry import SKYRIM_UNITS_PER_METRE as S
from gltf2nif.geometry import Mesh, convex_hull_planes, gltf_to_skyrim_point
from gltf2nif.nif_writer import build_nif
from nif2gltf.nif_reader import read_nif
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header


def _tri() -> Mesh:
    return Mesh(name="tri",
                positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                normals=[(0.0, 0.0, 1.0)] * 3,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                triangles=[(0, 1, 2)], material="mywall")


def _cube() -> Mesh:
    from tests.gltf2nif_fixtures import CUBE_POS, CUBE_TRIS
    pos = [tuple(map(float, p)) for p in CUBE_POS]
    return Mesh(name="cube", positions=pos, normals=[], uvs=[(0.0, 0.0)] * len(pos),
                triangles=CUBE_TRIS, material="box")


def test_single_triangle_roundtrip():
    data = build_nif([_tri()], "textures\\t", [False])
    meshes = read_nif(data)
    assert len(meshes) == 1
    m = meshes[0]
    assert [tuple(t) for t in m.triangles] == [(0, 1, 2)]
    # positions come back as gltf_to_skyrim(p) then nif2gltf axis swap = S * original.
    for got, src in zip(m.positions, _tri().positions):
        want = tuple(c * S for c in src)
        assert got == pytest.approx(want, abs=1e-2)


def test_uv_roundtrip_halfprecision():
    m = read_nif(build_nif([_tri()], "t", [False]))[0]
    for got, src in zip(m.uvs, _tri().uvs):
        assert got == pytest.approx(src, abs=2e-3)  # half-precision UV tolerance


def test_coordinate_pin():
    # A single known vertex must land at the derived Skyrim units after round-trip.
    m = Mesh(positions=[(2.0, 3.0, 5.0), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
             normals=[(0, 0, 1)] * 3, uvs=[(0, 0)] * 3, triangles=[(0, 1, 2)])
    got = read_nif(build_nif([m], "t", [False]))[0].positions[0]
    # gltf(2,3,5)->skyrim(2,-5,3)*S ; nif2gltf swaps back -> (2,3,5)*S
    assert got == pytest.approx((2.0 * S, 3.0 * S, 5.0 * S), abs=1e-1)


def test_cube_roundtrip():
    m = read_nif(build_nif([_cube()], "t", [False]))[0]
    assert len(m.triangles) == 12
    assert len(m.positions) == 8


def test_normals_present_when_computed():
    m = read_nif(build_nif([_cube()], "t", [False]))[0]  # cube has no glTF normals
    assert m.has_normals  # face normals were synthesised


def test_texture_paths_resolved():
    m = read_nif(build_nif([_tri()], "textures\\dsport\\m18", [True]))[0]
    assert m.texture == "textures\\dsport\\m18\\mywall.dds"


def test_texture_none_when_no_material():
    m = Mesh(positions=_tri().positions, normals=_tri().normals, uvs=_tri().uvs,
             triangles=[(0, 1, 2)], material="")
    assert read_nif(build_nif([m], "t", [False]))[0].texture == ""


def test_multi_shape():
    meshes = read_nif(build_nif([_tri(), _cube()], "t", [False, False]))
    assert len(meshes) == 2


def test_collision_blocks_present_and_unscaled():
    hull_pts = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], dtype=float)
    # transform to Z-up as load_hulls would (axis swap only)
    from gltf2nif.geometry import gltf_to_skyrim_dir
    zv = np.array([gltf_to_skyrim_dir(*p) for p in hull_pts])
    hull = Hull(zv, convex_hull_planes(zv))
    data = build_nif([_tri()], "t", [False], hulls=[hull])
    # geometry still round-trips (collision blocks skipped by reader)
    assert len(read_nif(data)) == 1
    h = _read_header(_Reader(data))
    assert "bhkConvexVerticesShape" in h["types"]
    # single hull hangs straight off the rigid body (vanilla Basket01 pattern)
    assert "bhkListShape" not in h["types"]
    assert "bhkRigidBody" in h["types"]
    assert "bhkCollisionObject" in h["types"]
    # two hulls go through a bhkListShape; bhk chain sits BEFORE the meshes,
    # children before parents (the engine's sequential loader cannot handle
    # forward refs inside the bhk chain — in-game CTD otherwise)
    data2 = build_nif([_tri()], "t", [False], hulls=[hull, hull])
    h2 = _read_header(_Reader(data2))
    assert "bhkListShape" in h2["types"]
    order = h2["types"]
    bt = [order[i] for i in h2["type_index"]] if "type_index" in h2 else None
    if bt:
        assert bt.index("bhkConvexVerticesShape") < bt.index("bhkListShape") \
            < bt.index("bhkRigidBody") < bt.index("bhkCollisionObject") < bt.index("BSTriShape")
    # convex vertices are Havok metres (max coord ~1, NOT ~70)
    i = h["types"].index("bhkConvexVerticesShape")
    o = h["offsets"][i]
    nv = struct.unpack_from("<I", data, o + 4 + 4 + 12 + 12)[0]
    vbase = o + 4 + 4 + 12 + 12 + 4
    verts = [struct.unpack_from("<4f", data, vbase + k * 16) for k in range(nv)]
    assert max(abs(c) for v in verts for c in v[:3]) < 5.0  # metres, not units


def test_footer_follows_last_block():
    # Blocks end 8 bytes before EOF: NiFooter = Num Roots (1) + root ref (block 0).
    # The engine reads the footer after the last block; without it the runtime
    # parses garbage as the root count (heap corruption in-game).
    import struct
    data = build_nif([_tri(), _cube()], "t", [False, False])
    h = _read_header(_Reader(data))
    end = h["offsets"][-1] + h["block_sizes"][-1]
    assert end + 8 == len(data)
    assert struct.unpack_from("<Ii", data, end) == (1, 0)


def test_too_many_verts_rejected():
    big = Mesh(positions=[(0.0, 0.0, 0.0)] * 70000, normals=[], uvs=[], triangles=[(0, 1, 2)])
    with pytest.raises(ValueError):
        build_nif([big], "t", [False])


def test_coplanar_hull_extruded_not_dropped(tmp_path):
    # A perfectly flat floor patch (4 coplanar verts) must survive as a thickened
    # hull, not be silently dropped (holes in walkable collision otherwise).
    import json as _json
    from gltf2nif.collision import load_hulls
    p = tmp_path / "flat.hulls.json"
    p.write_text(_json.dumps({"hulls": [
        {"vertices": [[0, 5, 0], [4, 5, 0], [4, 5, -4], [0, 5, -4]]}
    ]}))
    hulls = load_hulls(str(p))
    assert len(hulls) == 1
    h = hulls[0]
    assert len(h.vertices) == 8 and h.planes
    zs = sorted({round(float(v[2]), 3) for v in h.vertices})
    assert zs == [4.975, 5.025]  # Y-up 5m floor -> Z-up, +-2.5cm extrusion

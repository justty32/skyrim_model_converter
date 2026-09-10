"""Automatic collision: bounds, transforms and real CLI-produced NIF bytes."""

import json
import struct
import subprocess
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pytest

from any2nif.collision import collision_hulls
from any2nif.errors import AnyError
from gltf2nif.geometry import Mesh
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif
from tests.gltf2nif_fixtures import CUBE_POS, CUBE_TRIS, write_gltf_interleaved


def _mesh(points):
    return Mesh(positions=points, triangles=[(0, 1, 2), (1, 2, 3)])


def test_box_covers_all_meshes_but_ignores_unused_vertices():
    meshes = [_mesh([(1, 2, 3), (4, 2, 3), (1, 6, 3), (1, 2, 8), (999, 999, 999)]),
              _mesh([(-2, 1, 0), (0, 1, 0), (-2, 3, 0), (-2, 1, 2)])]
    hull, = collision_hulls("box", meshes)
    np.testing.assert_allclose(hull.vertices.min(axis=0), [-2, -8, 1])
    np.testing.assert_allclose(hull.vertices.max(axis=0), [4, 0, 6])
    assert len(hull.vertices) == 8
    assert len(hull.planes) == 6
    for normal, offset in hull.planes:
        assert np.max(hull.vertices @ normal + offset) < 1e-8


def test_flat_box_gets_physical_thickness():
    mesh = _mesh([(0, 5, 0), (4, 5, 0), (4, 5, 4), (0, 5, 4)])
    hull, = collision_hulls("box", [mesh])
    assert sorted(set(hull.vertices[:, 2])) == pytest.approx([4.975, 5.025])
    assert len(hull.planes) == 6


def test_convex_uses_outer_shape_and_fills_concavities():
    points = list(product([0.0, 2.0], repeat=3))
    points.extend([(1, 1, 1), (1, 1, 1)])  # duplicate/interior points do not reach the shell
    triangles = [(0, i, i + 1) for i in range(1, len(points) - 1)]
    hull, = collision_hulls("convex", [Mesh(positions=points, triangles=triangles)])
    assert len(hull.vertices) == 8
    np.testing.assert_allclose(hull.vertices.min(axis=0), [0, -2, 0])
    np.testing.assert_allclose(hull.vertices.max(axis=0), [2, 0, 2])
    assert all(np.max(hull.vertices @ normal + offset) < 1e-8
               for normal, offset in hull.planes)


def test_convex_large_mesh_does_not_use_bruteforce_plane_search():
    rng = np.random.default_rng(20260910)
    points = list(product([-1.0, 1.0], repeat=3))
    points.extend(map(tuple, rng.uniform(-0.99, 0.99, size=(20_000, 3))))
    triangles = [(0, i, i + 1) for i in range(1, len(points) - 1)]
    hull, = collision_hulls("convex", [Mesh(positions=points, triangles=triangles)])
    assert len(hull.vertices) == 8
    assert len(hull.planes) == 12  # scipy returns the cube as twelve triangular facets


def test_flat_convex_gets_physical_thickness_and_deduplicates():
    points = [(0, 5, 0), (4, 5, 0), (4, 5, 4), (0, 5, 4), (0, 5, 0)]
    hull, = collision_hulls("convex", [_mesh(points)])
    assert hull.vertices[:, 2].min() == pytest.approx(4.975)
    assert hull.vertices[:, 2].max() == pytest.approx(5.025)
    assert len(hull.vertices) == 8


@pytest.mark.parametrize("points", [
    [(0, 0, 0)] * 4,
    [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)],
])
def test_degenerate_convex_is_rejected(points):
    with pytest.raises(AnyError, match="automatic collision"):
        collision_hulls("convex", [_mesh(points)])


@pytest.mark.parametrize("mode", ["box", "convex", "convex-mesh"])
@pytest.mark.parametrize("points", [[], [(float("nan"), 0, 0)] * 4,
                                   [(float("inf"), 0, 0)] * 4])
def test_unusable_geometry_is_rejected(mode, points):
    mesh = _mesh(points) if points else Mesh()
    with pytest.raises(AnyError, match="automatic collision"):
        collision_hulls(mode, [mesh])


def test_none_and_json_keep_existing_contract(tmp_path):
    assert collision_hulls(None, []) is None
    assert collision_hulls("none", []) is None
    source = tmp_path / "hulls.json"
    source.write_text(json.dumps({"hulls": [{"vertices": list(product([0, 1], repeat=3))}]}))
    hull, = collision_hulls(str(source), [])
    np.testing.assert_allclose(hull.vertices.min(axis=0), [0, -1, 0])
    np.testing.assert_allclose(hull.vertices.max(axis=0), [1, 0, 1])


def test_per_mesh_convex_preserves_doorway_between_touching_parts():
    meshes = []
    for origin, size in [((0, 0, 0), (0.4, 2, 0.4)),
                         ((1.6, 0, 0), (0.4, 2, 0.4)),
                         ((0.4, 1.6, 0), (1.2, 0.4, 0.4))]:
        points = [tuple(origin[i] + p[i] * size[i] for i in range(3)) for p in CUBE_POS]
        meshes.append(Mesh(positions=points, triangles=CUBE_TRIS))
    hulls = collision_hulls("convex-mesh", meshes)
    assert len(hulls) == 3
    doorway = np.array([1.0, -0.2, 0.8])  # Havok Z-up metres

    def contains(hull, point):
        return all(point @ normal + offset <= 1e-8 for normal, offset in hull.planes)

    assert not any(contains(hull, doorway) for hull in hulls)
    assert contains(collision_hulls("convex", meshes)[0], doorway)
    for mesh, hull in zip(meshes, hulls):
        points = np.asarray(mesh.positions)[:, [0, 2, 1]]
        points[:, 1] *= -1
        assert all(contains(hull, point) for point in points)


def test_per_mesh_convex_rejects_bad_part_instead_of_dropping_collision():
    good = Mesh(positions=CUBE_POS, triangles=CUBE_TRIS)
    bad = Mesh(name="broken_leg", positions=[(0, 0, 0)] * 3, triangles=[(0, 1, 2)])
    with pytest.raises(AnyError, match="mesh 1.*broken_leg"):
        collision_hulls("convex-mesh", [good, bad])
    with pytest.raises(AnyError, match="no meshes"):
        collision_hulls("convex-mesh", [])


def _read_collision_vertices(data):
    header = _read_header(_Reader(data))
    vertices = []
    for block, block_type in enumerate(header["types"]):
        if block_type != "bhkConvexVerticesShape":
            continue
        offset = header["offsets"][block]
        count, = struct.unpack_from("<I", data, offset + 32)
        vertices.extend(struct.unpack_from("<4f", data, offset + 36 + i * 16)[:3]
                        for i in range(count))
    return np.asarray(vertices)


@pytest.mark.parametrize("axis, expected_low, expected_high", [
    ("y", [2, -10, 4], [4, -6, 8]),
    ("z", [2, 4, 6], [4, 8, 10]),
])
def test_real_cli_collision_matches_transformed_render_bounds(tmp_path, axis,
                                                              expected_low, expected_high):
    source = tmp_path / "box.gltf"
    positions = [(100 + x * 100, 200 + y * 200, 300 + z * 200) for x, y, z in CUBE_POS]
    write_gltf_interleaved(source.as_posix(), [{
        "positions": positions, "normals": [(0, 1, 0)] * 8,
        "uvs": [(0, 0)] * 8, "triangles": CUBE_TRIS,
    }])
    out = tmp_path / "box.nif"
    proc = subprocess.run([sys.executable, "-m", "any2nif", str(source), str(out),
                           "--unit", "cm", "--scale", "2", "--up-axis", axis,
                           "--collision", "box"],
                          cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = out.read_bytes()
    collision = _read_collision_vertices(data)
    assert len(collision) == 8
    np.testing.assert_allclose(collision.min(axis=0), expected_low)
    np.testing.assert_allclose(collision.max(axis=0), expected_high)
    # The reader returns glTF axes, still in Skyrim render units.
    render = np.asarray(read_nif(data)[0].positions) / 70.03
    render = render[:, [0, 2, 1]].copy()
    render[:, 1] *= -1
    np.testing.assert_allclose(render.min(axis=0), collision.min(axis=0), atol=1e-5)
    np.testing.assert_allclose(render.max(axis=0), collision.max(axis=0), atol=1e-5)


def test_real_cli_convex_collision(tmp_path):
    source = tmp_path / "convex.gltf"
    write_gltf_interleaved(source.as_posix(), [{
        "positions": CUBE_POS, "normals": [(0, 1, 0)] * 8,
        "uvs": [(0, 0)] * 8, "triangles": CUBE_TRIS,
    }])
    out = tmp_path / "convex.nif"
    proc = subprocess.run([sys.executable, "-m", "any2nif", str(source), str(out),
                           "--collision", "convex"],
                          cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    collision = _read_collision_vertices(out.read_bytes())
    assert len(collision) == 8
    np.testing.assert_allclose(collision.min(axis=0), [0, -1, 0])
    np.testing.assert_allclose(collision.max(axis=0), [1, 0, 1])


def test_package_per_mesh_hulls_follow_instances_and_preserve_gap(tmp_path):
    from any2nif.cli import main
    from any2nif.package import MANIFEST

    source = tmp_path / "parts.gltf"
    write_gltf_interleaved(source.as_posix(), [{
        "positions": [(x * 100, y * 100, z * 100) for x, y, z in CUBE_POS],
        "normals": [(0, 1, 0)] * 8, "uvs": [(0, 0)] * 8, "triangles": CUBE_TRIS,
    }])
    doc = json.loads(source.read_text())
    # Two instances of ONE source mesh must produce two independent hulls.
    doc["nodes"].append({"mesh": 0, "translation": [300, 0, 0]})
    doc["scenes"][0]["nodes"].append(1)
    source.write_text(json.dumps(doc))
    output = tmp_path / "data"
    args = [str(source), str(output), "--package", "--collision", "convex-mesh",
            "--unit", "cm", "--scale", "2", "--up-axis", "z"]
    assert main(args) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    assert manifest["collision"] == "convex-mesh"
    assert manifest["textures"]
    data = (output / manifest["mesh"]).read_bytes()
    header = _read_header(_Reader(data))
    hull_refs = [i for i, kind in enumerate(header["types"]) if kind == "bhkConvexVerticesShape"]
    assert len(hull_refs) == 2
    list_ref = header["types"].index("bhkListShape")
    reader = _Reader(data)
    reader.seek(header["offsets"][list_ref])
    assert reader.u32() == 2
    assert [reader.i32(), reader.i32()] == hull_refs
    rb_ref = header["types"].index("bhkRigidBody")
    reader.seek(header["offsets"][rb_ref])
    assert reader.i32() == list_ref
    points = _read_collision_vertices(data)
    assert len(points) == 16
    np.testing.assert_allclose(points[:8].min(axis=0), [0, 0, 0])
    np.testing.assert_allclose(points[:8].max(axis=0), [2, 2, 2])
    np.testing.assert_allclose(points[8:].min(axis=0), [6, 0, 0])
    np.testing.assert_allclose(points[8:].max(axis=0), [8, 2, 2])
    # A bad additional part must not publish a package with missing collision.
    snapshot = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    doc["nodes"][1]["scale"] = [0, 0, 0]
    source.write_text(json.dumps(doc))
    assert main(args) != 0
    assert {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()} == snapshot


def test_large_source_primitive_keeps_one_hull_after_render_splitting(tmp_path):
    from any2nif.cli import main
    from any2nif.package import MANIFEST

    count = 65_538
    positions = [CUBE_POS[i % 8] for i in range(count)]
    triangles = [(i, i + 1, i + 2) for i in range(0, count, 3)]
    source = tmp_path / "large.gltf"
    write_gltf_interleaved(source.as_posix(), [{
        "positions": positions, "normals": [(0, 1, 0)] * count,
        "uvs": [(0, 0)] * count, "triangles": triangles,
    }])
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package", "--collision", "convex-mesh"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    data = (output / manifest["mesh"]).read_bytes()
    types = _read_header(_Reader(data))["types"]
    assert types.count("BSTriShape") == 2
    assert types.count("bhkConvexVerticesShape") == 1
    assert "bhkListShape" not in types
    assert sum(len(mesh.triangles) for mesh in read_nif(data)) == len(triangles)
    points = _read_collision_vertices(data)
    np.testing.assert_allclose(points.min(axis=0), [0, -1, 0])
    np.testing.assert_allclose(points.max(axis=0), [1, 0, 1])

"""Scene instances and transforms survive the real COLLADA -> any2nif path."""

from __future__ import annotations

import json
import struct
from itertools import product

import numpy as np

from any2nif import normalize_to_gltf
from any2nif.cli import main
from any2nif.package import MANIFEST
from gltf2nif import read_gltf
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif


_CORNERS = np.asarray(list(product((0.0, 1.0), repeat=3)))
_TRIANGLES = np.asarray([
    (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3),
    (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
    (0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5),
])
_PARENT = np.asarray([[0, 0, 1, 2], [0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 0, 1.]])
_CHILDREN = (
    np.eye(4),
    np.asarray([[-2, 0, 0, 3], [0, .5, 0, 0], [0, 0, 1.5, 0], [0, 0, 0, 1.]]),
)


def _transform_corners(matrix):
    homogeneous = np.column_stack((_CORNERS, np.ones(len(_CORNERS))))
    return (matrix @ homogeneous.T).T[:, :3]


_EXPECTED_BOUNDS = [
    (points.min(axis=0), points.max(axis=0))
    for points in (_transform_corners(_PARENT @ child) for child in _CHILDREN)
]
_EXPECTED_BOUNDS.sort(key=lambda pair: tuple(pair[0]))


def _write_instanced_dae(path):
    """One cube geometry, instanced below a translated parent twice.

    The second child has a negative-determinant, non-uniform scale.  This is
    deliberately hand-authored COLLADA rather than a mocked trimesh Scene: it
    exercises the project's supported .dae loader through pycollada.
    """
    vertices = " ".join(str(value) for point in _CORNERS for value in point)
    normals_array = (_CORNERS - .5) / np.sqrt(.75)
    normals = " ".join(str(value) for normal in normals_array for value in normal)
    # Outward-facing triangles for product-order cube corners.
    triangles = " ".join(f"{index} {index}" for index in _TRIANGLES.flat)
    path.write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit meter="1" name="meter"/><up_axis>Y_UP</up_axis></asset>
  <library_geometries>
    <geometry id="cube" name="cube"><mesh>
      <source id="cube-positions">
        <float_array id="cube-positions-array" count="24">{vertices}</float_array>
        <technique_common><accessor source="#cube-positions-array" count="8" stride="3">
          <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
        </accessor></technique_common>
      </source>
      <source id="cube-normals">
        <float_array id="cube-normals-array" count="24">{normals}</float_array>
        <technique_common><accessor source="#cube-normals-array" count="8" stride="3">
          <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
        </accessor></technique_common>
      </source>
      <vertices id="cube-vertices"><input semantic="POSITION" source="#cube-positions"/></vertices>
      <triangles count="12"><input semantic="VERTEX" source="#cube-vertices" offset="0"/>
        <input semantic="NORMAL" source="#cube-normals" offset="1"/><p>{triangles}</p></triangles>
    </mesh></geometry>
  </library_geometries>
  <library_visual_scenes><visual_scene id="Scene" name="Scene">
    <node id="translated-parent" name="translated-parent">
      <matrix>0 0 1 2  0 1 0 0  -1 0 0 0  0 0 0 1</matrix>
      <node id="plain-instance" name="plain-instance"><instance_geometry url="#cube"/></node>
      <node id="mirrored-instance" name="mirrored-instance">
        <matrix>-2 0 0 3  0 0.5 0 0  0 0 1.5 0  0 0 0 1</matrix>
        <instance_geometry url="#cube"/>
      </node>
    </node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#Scene"/></scene>
</COLLADA>
""", encoding="utf-8")
    return path


def _bounds(meshes, *, nif=False):
    result = []
    for mesh in meshes:
        points = np.asarray(mesh.positions, dtype=float)
        if nif:
            # NIF reader has already converted back to glTF axes; return metres.
            points = points / 70.03
        result.append((points.min(axis=0), points.max(axis=0)))
    return sorted(result, key=lambda pair: tuple(pair[0]))


def _collision_bounds(data):
    header = _read_header(_Reader(data))
    result = []
    for block, kind in enumerate(header["types"]):
        if kind != "bhkConvexVerticesShape":
            continue
        offset = header["offsets"][block]
        count, = struct.unpack_from("<I", data, offset + 32)
        points = np.asarray([
            struct.unpack_from("<4f", data, offset + 36 + index * 16)[:3]
            for index in range(count)
        ])
        # Havok is already in metres, but uses Skyrim Z-up axes.
        points = points[:, [0, 2, 1]]
        points[:, 2] *= -1
        result.append((points.min(axis=0), points.max(axis=0)))
    return sorted(result, key=lambda pair: tuple(pair[0]))


def _assert_bounds(actual):
    assert len(actual) == len(_EXPECTED_BOUNDS)
    for pair, expected in zip(actual, _EXPECTED_BOUNDS):
        np.testing.assert_allclose(pair[0], expected[0], atol=1e-6)
        np.testing.assert_allclose(pair[1], expected[1], atol=1e-6)


def _triangle_signature(points):
    """Sort triangles while preserving each triangle's cyclic winding."""
    canonical = []
    for triangle in np.round(points, decimals=6):
        first = min(range(3), key=lambda index: tuple(triangle[index]))
        canonical.append(np.roll(triangle, -first, axis=0))
    canonical.sort(key=lambda triangle: tuple(triangle.flat))
    return np.asarray(canonical)


def _assert_face_corners(meshes, *, nif=False):
    actual = []
    for mesh in meshes:
        points = np.asarray(mesh.positions, dtype=float)
        if nif:
            points /= 70.03
        actual.append(_triangle_signature(points[np.asarray(mesh.triangles)]))
    actual.sort(key=lambda points: tuple(points[0].flat))
    expected = []
    for child in _CHILDREN:
        transformed = _transform_corners(_PARENT @ child)
        triangles = _TRIANGLES if np.linalg.det(child[:3, :3]) > 0 else _TRIANGLES[:, [0, 2, 1]]
        expected.append(_triangle_signature(transformed[triangles]))
    expected.sort(key=lambda points: tuple(points[0].flat))
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        np.testing.assert_allclose(got, want, atol=1e-6)


def _assert_winding_matches_normals(meshes):
    for mesh in meshes:
        positions = np.asarray(mesh.positions)
        normals = np.asarray(mesh.normals)
        for triangle in mesh.triangles:
            a, b, c = positions[np.asarray(triangle)]
            face_normal = np.cross(b - a, c - a)
            vertex_normal = normals[np.asarray(triangle)].mean(axis=0)
            assert np.dot(face_normal, vertex_normal) > 0


def test_normalize_dae_preserves_nested_transforms_and_mirrored_instance(tmp_path):
    source = _write_instanced_dae(tmp_path / "instances.dae")

    normalized = normalize_to_gltf(str(source), str(tmp_path))
    meshes = read_gltf(normalized)

    _assert_bounds(_bounds(meshes))
    _assert_face_corners(meshes)
    _assert_winding_matches_normals(meshes)


def test_package_dae_keeps_render_instances_and_convex_mesh_groups(tmp_path):
    source = _write_instanced_dae(tmp_path / "instances.dae")
    output = tmp_path / "Data"

    assert main([str(source), str(output), "--package", "--collision", "convex-mesh",
                 "--bake-size", "64"]) == 0
    manifest = json.loads((output / MANIFEST).read_text(encoding="utf-8"))
    data = (output / manifest["mesh"]).read_bytes()

    meshes = read_nif(data)
    _assert_bounds(_bounds(meshes, nif=True))
    _assert_face_corners(meshes, nif=True)
    _assert_winding_matches_normals(meshes)
    _assert_bounds(_collision_bounds(data))

"""Oversized mesh splitting preserves the NIF writer's geometry contract."""

import numpy as np
import pytest

from any2nif.errors import AnyError
from any2nif.mesh_split import split_meshes
from gltf2nif.geometry import Mesh, SKYRIM_UNITS_PER_METRE
from gltf2nif.nif_writer import build_nif
from nif2gltf.nif_reader import read_nif


def _mesh(count, triangles):
    positions = [(float(i), float(i % 7), float(i % 11)) for i in range(count)]
    return Mesh(
        name="large",
        positions=positions,
        normals=[(0.0, 1.0, 0.0)] * count,
        uvs=[(i / max(count, 1), 0.25) for i in range(count)],
        colors=[(1.0, 0.5, 0.25, 1.0)] * count,
        triangles=triangles,
        material="stone",
        material_index=7,
    )


def test_mesh_at_limit_is_returned_unchanged():
    mesh = _mesh(6, [(0, 1, 2), (3, 4, 5)])
    result = split_meshes([mesh], max_vertices=6)
    assert result == [mesh]
    assert result[0] is mesh


def test_batches_preserve_triangle_order_attributes_and_shared_indices():
    mesh = _mesh(8, [(0, 1, 2), (2, 1, 3), (4, 5, 6), (6, 5, 7)])
    parts = split_meshes([mesh], max_vertices=4)
    assert [part.triangles for part in parts] == [
        [(0, 1, 2), (2, 1, 3)], [(0, 1, 2), (2, 1, 3)],
    ]
    assert [len(part.positions) for part in parts] == [4, 4]
    assert [part.name for part in parts] == ["large_part_001", "large_part_002"]
    assert all(part.material == "stone" and part.material_index == 7 for part in parts)
    assert parts[0].normals == [mesh.normals[i] for i in (0, 1, 2, 3)]
    assert parts[0].uvs == [mesh.uvs[i] for i in (0, 1, 2, 3)]
    assert parts[0].colors == [mesh.colors[i] for i in (0, 1, 2, 3)]


def test_large_mesh_writes_and_reads_back_all_triangle_geometry():
    count = 65_538
    triangles = [(i, i + 1, i + 2) for i in range(0, count, 3)]
    mesh = _mesh(count, triangles)
    parts = split_meshes([mesh])
    assert [len(part.positions) for part in parts] == [65_535, 3]
    assert sum(len(part.triangles) for part in parts) == len(triangles)

    data = build_nif(parts, r"textures\split", [False] * len(parts))
    readback = read_nif(data)
    assert sum(len(part.triangles) for part in readback) == len(triangles)
    assert all(part.texture == r"textures\split\stone.dds" for part in readback)
    assert all(part.has_normals and part.has_uvs for part in readback)
    # Reader returns Skyrim units with the inverse axis swap already applied. Compare
    # triangle vertices in order, rather than merely trusting equal triangle counts.
    expected = np.asarray([[mesh.positions[index] for index in triangle]
                           for triangle in mesh.triangles]) * SKYRIM_UNITS_PER_METRE
    actual = np.asarray([[part.positions[index] for index in triangle]
                         for part in readback for triangle in part.triangles])
    np.testing.assert_allclose(actual, expected, atol=1e-2)


@pytest.mark.parametrize("maximum", [True, 2, 3.5])
def test_invalid_limit_is_rejected(maximum):
    with pytest.raises(AnyError, match="max_vertices"):
        split_meshes([], maximum)


@pytest.mark.parametrize("field,value", [
    ("triangles", [(0, 1)]),
    ("triangles", [(0, 1, 99)]),
    ("normals", [(0, 1, 0)]),
    ("positions", [(0, 0, float("nan")), (1, 0, 0), (0, 1, 0)]),
])
def test_invalid_mesh_data_is_rejected(field, value):
    mesh = Mesh(positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], triangles=[(0, 1, 2)])
    setattr(mesh, field, value)
    with pytest.raises(AnyError, match="mesh split"):
        split_meshes([mesh], max_vertices=3)

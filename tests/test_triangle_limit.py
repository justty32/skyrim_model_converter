"""NIF's 16-bit triangle count is enforced alongside its vertex limit."""

import numpy as np
import pytest

from any2nif.errors import AnyError
from any2nif.mesh_split import split_meshes
from gltf2nif.geometry import Mesh, SKYRIM_UNITS_PER_METRE
from gltf2nif.nif_writer import build_nif
from nif2gltf.nif_reader import read_nif
from tests.test_any2nif_tangents import _frames


def _shared_grid(columns=183, rows=183):
    """Return a nondegenerate grid with shared vertices and authored tangents."""
    positions = [(column / (columns - 1), 0.0, row / (rows - 1))
                 for row in range(rows) for column in range(columns)]
    normals = [(0.0, 1.0, 0.0)] * len(positions)
    uvs = [(position[0], position[2]) for position in positions]
    tangents = [(1.0, 0.0, 0.0, 1.0)] * len(positions)
    triangles = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            top_left = row * columns + column
            bottom_left = top_left + columns
            triangles.extend(((top_left, bottom_left, top_left + 1),
                              (top_left + 1, bottom_left, bottom_left + 1)))
    return Mesh(name="shared_grid", positions=positions, normals=normals, uvs=uvs,
                tangents=tangents, triangles=triangles, material="grid", material_index=4)


def test_triangle_limit_boundary_uses_forced_small_limit():
    mesh = _shared_grid(columns=3, rows=2)  # Four triangles over six shared vertices.
    assert split_meshes([mesh], max_vertices=6, max_triangles=4) == [mesh]
    assert split_meshes([mesh], max_vertices=6, max_triangles=4)[0] is mesh

    parts = split_meshes([mesh], max_vertices=6, max_triangles=3)
    assert [len(part.triangles) for part in parts] == [3, 1]
    assert [triangle for part in parts for triangle in part.triangles] == [
        (0, 1, 2), (2, 1, 3), (2, 3, 4), (0, 1, 2),
    ]
    assert all(part.material == "grid" and part.material_index == 4 for part in parts)
    assert all(len(part.tangents) == len(part.positions) for part in parts)


@pytest.mark.parametrize("maximum", [True, 0, -1, 1.5])
def test_invalid_triangle_limit_is_rejected(maximum):
    with pytest.raises(AnyError, match="max_triangles"):
        split_meshes([], max_triangles=maximum)


def test_shared_grid_over_triangle_limit_roundtrips_without_loss():
    mesh = _shared_grid()
    assert len(mesh.positions) < 65_535
    assert len(mesh.triangles) > 65_535

    parts = split_meshes([mesh])
    assert [len(part.triangles) for part in parts] == [65_535, 713]
    assert all(len(part.triangles) <= 65_535 for part in parts)
    assert all(part.material == mesh.material and part.material_index == mesh.material_index
               for part in parts)
    assert all(len(part.tangents) == len(part.positions) for part in parts)

    data = build_nif(parts, r"textures\triangle-limit", [False] * len(parts))
    readback = read_nif(data)
    assert [len(part.triangles) for part in readback] == [65_535, 713]
    assert all(part.texture == r"textures\triangle-limit\grid.dds" for part in readback)

    expected = np.asarray([[mesh.positions[index] for index in triangle]
                           for triangle in mesh.triangles]) * SKYRIM_UNITS_PER_METRE
    actual = np.asarray([[part.positions[index] for index in triangle]
                         for part in readback for triangle in part.triangles])
    np.testing.assert_allclose(actual, expected, atol=1e-2)

    frames = _frames(data)
    assert [len(frame) for frame in frames] == [len(part.positions) for part in parts]
    for frame in frames:
        np.testing.assert_allclose(frame[:, :, 0],
                                   np.tile((1.0, 0.0, 0.0), (len(frame), 1)),
                                   atol=1 / 127)


def test_writer_rejects_triangle_count_overflow_before_serializing():
    mesh = _shared_grid(columns=3, rows=2)
    mesh.name = "overflow"
    mesh.triangles = [mesh.triangles[0]] * 65_536

    with pytest.raises(ValueError, match=r"overflow.*65536 triangles.*65535.*split"):
        build_nif([mesh], "textures\\test", [False])

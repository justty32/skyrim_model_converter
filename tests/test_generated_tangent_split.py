"""Generated tangent handedness across NIF-safe mesh splitting."""

import numpy as np

from any2nif.mesh_split import split_meshes
from gltf2nif import read_gltf
from gltf2nif.geometry import Mesh
from gltf2nif.nif_writer import build_nif
from tests.gltf2nif_fixtures import write_gltf_interleaved
from tests.test_any2nif_tangents import _frames


def _mirrored_mesh(triangle_count, *, preserve=False):
    positions, normals, uvs, triangles = [], [], [], []
    for triangle in range(triangle_count):
        start = len(positions)
        # UV orientation is opposite the +Z geometric face. Its derivatives are
        # dP/du=+Y and dP/dv=+X, while cross(N,T) points toward -X.
        positions.extend(((0.0, 0.0, float(triangle)),
                          (1.0, 0.0, float(triangle)),
                          (0.0, 1.0, float(triangle))))
        normals.extend(((0.0, 0.0, 1.0),) * 3)
        uvs.extend(((0.0, 0.0), (0.0, 1.0), (1.0, 0.0)))
        triangles.append((start, start + 1, start + 2))
    return Mesh(positions=positions, normals=normals, uvs=uvs,
                triangles=triangles, uv_handedness=preserve)


def test_read_gltf_handedness_is_opt_in_even_without_authored_tangent(tmp_path):
    path = tmp_path / "mirrored.gltf"
    mesh = _mirrored_mesh(1)
    write_gltf_interleaved(str(path), [{
        "positions": mesh.positions, "normals": mesh.normals,
        "uvs": mesh.uvs, "triangles": mesh.triangles,
    }])
    legacy, = read_gltf(str(path))
    preserved, = read_gltf(str(path), preserve_tangents=True)
    assert legacy.tangents == preserved.tangents == []
    assert legacy.uv_handedness is False
    assert preserved.uv_handedness is True

    legacy_bytes = build_nif([legacy], "textures\\test", [False])
    preserved_bytes = build_nif([preserved], "textures\\test", [False])
    assert legacy_bytes != preserved_bytes
    legacy_frame, = _frames(legacy_bytes)
    preserved_frame, = _frames(preserved_bytes)
    np.testing.assert_allclose(legacy_frame[:, :, 0],
                               np.tile([0, 1, 0], (3, 1)), atol=1 / 127)
    np.testing.assert_allclose(legacy_frame[:, :, 1],
                               np.tile([-1, 0, 0], (3, 1)), atol=1 / 127)
    np.testing.assert_allclose(preserved_frame[:, :, 1],
                               np.tile([1, 0, 0], (3, 1)), atol=1 / 127)


def test_large_split_preserves_generated_derivative_handedness_in_every_shape():
    mesh = _mirrored_mesh(21_846, preserve=True)  # 65,538 vertices.
    parts = split_meshes([mesh])
    assert [len(part.positions) for part in parts] == [65_535, 3]
    assert all(part.uv_handedness is True and len(part.tangents) == len(part.positions) for part in parts)

    frames = _frames(build_nif(parts, "textures\\split", [False, False]))
    assert [len(frame) for frame in frames] == [65_535, 3]
    for part, frame in zip(parts, frames):
        triangle = part.triangles[0]
        p = np.asarray(part.positions)[list(triangle)]
        uv = np.asarray(part.uvs)[list(triangle)]
        edge1, edge2 = p[1] - p[0], p[2] - p[0]
        duv1, duv2 = uv[1] - uv[0], uv[2] - uv[0]
        determinant = duv1[0] * duv2[1] - duv2[0] * duv1[1]
        dpdu = (duv2[1] * edge1 - duv1[1] * edge2) / determinant
        dpdv = (-duv2[0] * edge1 + duv1[0] * edge2) / determinant
        dpdu /= np.linalg.norm(dpdu)
        dpdv /= np.linalg.norm(dpdv)
        np.testing.assert_allclose(frame[:, :, 0],
                                   np.tile(dpdu, (len(frame), 1)), atol=1 / 127)
        np.testing.assert_allclose(frame[:, :, 1],
                                   np.tile(dpdv, (len(frame), 1)), atol=1 / 127)


def test_legacy_flag_false_retains_previous_generated_nif_bytes():
    implicit = _mirrored_mesh(2)
    explicit = _mirrored_mesh(2, preserve=False)
    assert build_nif([implicit], "textures\\legacy", [False]) == build_nif(
        [explicit], "textures\\legacy", [False])

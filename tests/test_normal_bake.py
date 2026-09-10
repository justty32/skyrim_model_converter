"""Tangent-basis conversion used by cross-UV normal-map baking."""

import numpy as np
import pytest

from any2nif.normal_bake import (
    interpolate_frames,
    reorient_normals,
    source_frames,
    target_corner_frames,
    target_frames,
)
from gltf2nif.geometry import compute_tangents


POSITIONS = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
NORMALS = [(0, 0, 1)] * 3
TRIANGLES = [(0, 1, 2)]
UVS = [(0, 0), (1, 0), (0, 1)]


def _sample(vector, alpha=0.37):
    vector = np.asarray(vector, dtype=float)
    return np.r_[vector / np.linalg.norm(vector) * 0.5 + 0.5, alpha][None, :]


def _decoded(sample):
    vector = sample[0, :3] * 2 - 1
    return vector / np.linalg.norm(vector)


def test_generated_source_basis_and_mirrored_uv_handedness():
    regular = source_frames(POSITIONS, NORMALS, UVS, TRIANGLES)
    mirrored = source_frames(POSITIONS, NORMALS,
                             [(0, 0), (-1, 0), (0, 1)], TRIANGLES)
    assert regular[0] == pytest.approx(np.eye(3))
    assert mirrored[0, :, 0] == pytest.approx((-1, 0, 0))
    assert mirrored[0, :, 1] == pytest.approx((0, 1, 0))
    assert np.linalg.det(mirrored[0]) == pytest.approx(-1)


def test_supplied_tangent_w_controls_bitangent():
    plus = source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                         [(1, 0, 0, 1)] * 3)
    minus = source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                          [(1, 0, 0, -1)] * 3)
    assert plus[0] == pytest.approx(np.eye(3))
    assert minus[0, :, 1] == pytest.approx((0, -1, 0))
    assert np.linalg.det(minus[0]) == pytest.approx(-1)


@pytest.mark.parametrize(("transform", "tangent", "bitangent"), [
    (np.array([[0, 1], [-1, 0]]), (0, -1, 0), (1, 0, 0)),  # mapped UV +90 degrees
    (np.array([[-1, 0], [0, 1]]), (-1, 0, 0), (0, 1, 0)),  # mirror U
])
def test_supplied_tangent_uses_normal_uv_linear_transform(transform, tangent, bitangent):
    frames = source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           [(1, 0, 0, 1)] * 3, uv_transform=transform)
    assert frames[0, :, 0] == pytest.approx(tangent)
    assert frames[0, :, 1] == pytest.approx(bitangent)


def test_supplied_tangent_nonuniform_uv_transform_acts_in_local_basis():
    root_half = 2 ** -0.5
    frames = source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           [(root_half, root_half, 0, 1)] * 3,
                           uv_transform=[[2, 0], [0, 0.5]])
    assert frames[0, :, 0] == pytest.approx((root_half, root_half, 0))
    assert frames[0, :, 1] == pytest.approx((-root_half, root_half, 0))


def test_target_frames_exactly_match_writer_basis():
    frames = target_frames(POSITIONS, NORMALS,
                           [(0, 0), (0, 1), (-1, 0)], TRIANGLES)
    tangents, bitangents = compute_tangents(
        np.asarray(POSITIONS), np.asarray(NORMALS),
        np.asarray([(0, 0), (0, 1), (-1, 0)]), np.asarray(TRIANGLES))
    np.testing.assert_allclose(frames[:, :, 0], tangents)
    np.testing.assert_allclose(frames[:, :, 1], bitangents)
    np.testing.assert_allclose(frames[:, :, 2], NORMALS)


def test_target_corner_frames_recomputes_basis_across_writer_split():
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)]
    normals = [(0, 0, 1)] * 5
    uvs = [(0, 0), (1, 0), (0, 1), (2, 0), (2, 1)]
    triangles = [(0, 1, 2), (1, 3, 4)]
    corners = target_corner_frames(positions, normals, uvs, triangles, max_vertices=4)
    assert corners.shape == (2, 3, 3, 3)
    # Shared source vertex 1 belongs to separate output pieces.  Its writer
    # tangent follows each piece's own UV derivative accumulation.
    assert corners[0, 1, :, 0] == pytest.approx((1, 0, 0))
    assert corners[1, 0, :, 0] == pytest.approx((0, 1, 0))


def test_reorient_normals_through_ninety_degree_basis():
    source = np.eye(3)[None, :, :]
    # Columns: target T=+Y, B=-X, N=+Z.
    target = np.array([[[0, -1, 0], [1, 0, 0], [0, 0, 1]]], dtype=float)
    result = reorient_normals(_sample((1, 0, 0)), source, target)
    assert _decoded(result) == pytest.approx((0, -1, 0))
    assert result[0, 3] == pytest.approx(0.37)


def test_reorient_mirrored_given_handedness():
    source = source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           [(1, 0, 0, -1)] * 3)[:1]
    result = reorient_normals(_sample((0, 1, 0)), source, np.eye(3)[None])
    assert _decoded(result) == pytest.approx((0, -1, 0))


@pytest.mark.parametrize(("scale", "expected"), [
    (0.0, (0, 0, 1)),
    (0.6, (0.6, 0, 1)),
    (-1.0, (-1, 0, 1)),
])
def test_reorient_applies_normal_scale_before_basis(scale, expected):
    result = reorient_normals(_sample((1, 0, 1)), np.eye(3)[None],
                              np.eye(3)[None], scale=scale)
    expected = np.asarray(expected, dtype=float)
    assert _decoded(result) == pytest.approx(expected / np.linalg.norm(expected))


def test_reorient_uses_inverse_for_nonorthogonal_interpolated_frame():
    target = np.array([[[1, 0.6, 0], [0, 0.8, 0], [0, 0, 1]]], dtype=float)
    tangent_normal = np.array((0.2, -0.4, 0.9))
    world = target[0] @ tangent_normal
    result = reorient_normals(_sample(world), np.eye(3)[None], target)
    assert _decoded(result) == pytest.approx(tangent_normal / np.linalg.norm(tangent_normal))


def test_interpolate_frames_normalizes_columns_without_orthogonalizing():
    frames = np.repeat(np.eye(3)[None], 3, axis=0)
    frames[1, :, 0] = (1, 1, 0)
    result = interpolate_frames(frames, TRIANGLES, [0], [[0.5, 0.5, 0]])
    assert np.linalg.norm(result[0], axis=0) == pytest.approx((1, 1, 1))
    assert np.dot(result[0, :, 0], result[0, :, 1]) > 0


@pytest.mark.parametrize("call, message", [
    (lambda: source_frames(POSITIONS, [(0, 0, 0)] * 3, UVS, TRIANGLES), "non-zero"),
    (lambda: source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           [(1, 0, 0, 0)] * 3), "handedness"),
    (lambda: source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           uv_transform=np.eye(2)), "supplied tangents"),
    (lambda: source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           [(1, 0, 0, 1)] * 3, uv_transform=[[1, np.nan], [0, 1]]),
     "finite 2x2"),
    (lambda: source_frames(POSITIONS, NORMALS, UVS, TRIANGLES,
                           [(1, 0, 0, 1)] * 3, uv_transform=[[0, 0], [0, 1]]),
     "invertible"),
    (lambda: interpolate_frames(np.eye(3)[None], [(0, 1, 2)], [0], [[1, 0, 0]]),
     "out of range"),
    (lambda: reorient_normals(np.array([[np.nan, .5, 1, 1]]),
                              np.eye(3)[None], np.eye(3)[None]), "finite"),
    (lambda: reorient_normals(_sample((0, 0, 1)), np.eye(3)[None],
                              np.zeros((1, 3, 3))), "singular"),
    (lambda: reorient_normals(_sample((0, 0, 1)), np.eye(3)[None],
                              np.eye(3)[None], scale=np.inf), "finite"),
])
def test_invalid_geometry_and_samples_are_rejected(call, message):
    with pytest.raises(ValueError, match=message):
        call()

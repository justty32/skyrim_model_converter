"""gltf2nif geometry: coordinate/scale transform, tangents, convex-hull half-spaces."""

from __future__ import annotations

import numpy as np
import pytest

from gltf2nif.geometry import (
    SKYRIM_UNITS_PER_METRE,
    convex_hull_planes,
    compute_tangents,
    face_normals,
    gltf_to_skyrim_dir,
    gltf_to_skyrim_point,
)
from nif2gltf.geometry import skyrim_to_gltf_point


def test_pinned_point():
    # glTF Y-up 1 metre along each axis -> Skyrim Z-up units, pinned exactly.
    assert gltf_to_skyrim_point(1.0, 2.0, 3.0) == pytest.approx(
        (1.0 * SKYRIM_UNITS_PER_METRE, -3.0 * SKYRIM_UNITS_PER_METRE, 2.0 * SKYRIM_UNITS_PER_METRE))


def test_inverse_of_nif2gltf():
    # gltf_to_skyrim then skyrim_to_gltf must return the point scaled by the unit factor
    # (axis exactly recovered; nif2gltf does not divide the scale back out).
    p = (1.3, -2.7, 4.1)
    sk = gltf_to_skyrim_point(*p)
    back = skyrim_to_gltf_point(*sk)
    assert back == pytest.approx(tuple(c * SKYRIM_UNITS_PER_METRE for c in p))


def test_dir_transform_no_scale():
    assert gltf_to_skyrim_dir(0.0, 1.0, 0.0) == pytest.approx((0.0, 0.0, 1.0))
    assert gltf_to_skyrim_dir(0.0, 0.0, 1.0) == pytest.approx((0.0, -1.0, 0.0))


def test_face_normals_unit():
    pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    n = face_normals(pos, [(0, 1, 2)])
    for v in n:
        assert v == pytest.approx((0.0, 0.0, 1.0))


def test_tangents_orthogonal_to_normal():
    pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    nrm = [(0, 0, 1)] * 3
    uv = [(0, 0), (1, 0), (0, 1)]
    tan, bit = compute_tangents(pos, nrm, uv, [(0, 1, 2)])
    for t, n in zip(tan, nrm):
        assert abs(np.dot(t, n)) < 1e-6
        assert np.linalg.norm(t) == pytest.approx(1.0, abs=1e-6)


def test_convex_hull_planes_tetra():
    verts = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], dtype=float)
    planes = convex_hull_planes(verts)
    assert len(planes) == 4  # a tetrahedron has 4 faces
    # every vertex satisfies every half-space (inside/on): n·v + d <= eps
    for n, d in planes:
        for v in verts:
            assert float(np.dot(n, v)) + d <= 1e-4


def test_convex_hull_planes_cube():
    verts = np.array([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=float)
    planes = convex_hull_planes(verts)
    assert len(planes) == 6  # a cube (deduped) has 6 face planes


def test_convex_hull_degenerate_returns_empty():
    flat = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)], dtype=float)  # coplanar
    assert convex_hull_planes(flat) == []

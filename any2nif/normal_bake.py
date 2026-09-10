"""Tangent-frame helpers for re-baking a glTF normal map onto new UVs.

Frames are arrays of matrices whose columns are tangent, bitangent, and normal.
Generated source tangents use a smooth Lengyel-style approximation, not
MikkTSpace.  The target path deliberately calls the NIF writer's implementation
so the baked map is interpreted by the same basis that is written to BSTriShape.

The supplied-tangent UV-transform behavior is informed by Khronos' Sample Renderer:
https://github.com/KhronosGroup/glTF-Sample-Renderer/blob/main/source/Renderer/shaders/primitive.vert
Its missing-tangent derivative basis is documented by the fragment shader:
https://github.com/KhronosGroup/glTF-Sample-Renderer/blob/main/source/Renderer/shaders/material_info.glsl
The renderer passes the sampling matrix from material.js to a world-space vector
multiply; here we instead apply the equivalent coordinate change in the local T/B
plane, which remains valid for arbitrarily oriented surfaces:
https://github.com/KhronosGroup/glTF-Sample-Renderer/blob/main/source/gltf/material.js
"""

from __future__ import annotations

import numpy as np

from gltf2nif.geometry import compute_tangents
from gltf2nif.geometry import Mesh
from gltf2nif.tangents import tangent_basis

from .mesh_split import split_meshes


_EPS = 1e-12


def _vertices(positions, normals, uvs):
    pos = np.asarray(positions, dtype=np.float64)
    nrm = np.asarray(normals, dtype=np.float64)
    tex = np.asarray(uvs, dtype=np.float64)
    if pos.ndim != 2 or pos.shape[1:] != (3,) or len(pos) == 0:
        raise ValueError("positions must be a non-empty Nx3 array")
    if nrm.shape != pos.shape:
        raise ValueError("normals must be an Nx3 array matching positions")
    if tex.shape != (len(pos), 2):
        raise ValueError("uvs must be an Nx2 array matching positions")
    if not np.isfinite(pos).all() or not np.isfinite(nrm).all() or not np.isfinite(tex).all():
        raise ValueError("positions, normals, and uvs must be finite")
    lengths = np.linalg.norm(nrm, axis=1)
    if (lengths <= _EPS).any():
        raise ValueError("normals must be non-zero")
    return pos, nrm / lengths[:, None], tex


def _triangles(triangles, vertex_count):
    tri = np.asarray(triangles)
    if tri.ndim != 2 or tri.shape[1:] != (3,) or len(tri) == 0:
        raise ValueError("triangles must be a non-empty Mx3 array")
    if not np.issubdtype(tri.dtype, np.integer):
        raise ValueError("triangle indices must be integers")
    tri = tri.astype(np.int64, copy=False)
    if (tri < 0).any() or (tri >= vertex_count).any():
        raise ValueError("triangle index is out of range")
    return tri


def _normalise_columns(frames, label):
    lengths = np.linalg.norm(frames, axis=1)
    if not np.isfinite(lengths).all() or (lengths <= _EPS).any():
        raise ValueError(f"{label} contains a zero or non-finite basis vector")
    return frames / lengths[:, None, :]


def source_frames(positions, normals, uvs, triangles, tangents=None, uv_transform=None):
    """Return per-vertex glTF source frames with columns ``T, B, N``.

    Supplied glTF FLOAT VEC4 tangents retain their W handedness.  ``uv_transform``
    is the 2x2 sampling matrix ``A`` under the row-vector convention
    ``mapped_uv = raw_uv @ A`` (the linear part of KHR_texture_transform; offset
    does not affect directions).  The mapped texture's basis is the original T/B
    pair times ``inverse(A).T``.  This coordinate-space operation must not be
    replaced with a transform of the world-space X/Y components.

    Without supplied tangents, tangent and bitangent directions are accumulated
    over adjacent UV triangles and ``uvs`` must already contain the mapped UVs;
    passing ``uv_transform`` on this path is rejected to prevent double mapping.
    The accumulated bitangent determines the sign of ``cross(N, T)``.
    Use source_corner_frames for generated frames on mirrored shared-vertex seams.
    """
    pos, nrm, tex = _vertices(positions, normals, uvs)
    tri = _triangles(triangles, len(pos))

    if tangents is not None:
        supplied = np.asarray(tangents, dtype=np.float64)
        if supplied.shape != (len(pos), 4) or not np.isfinite(supplied).all():
            raise ValueError("tangents must be a finite Nx4 array matching positions")
        if not np.all(np.isin(supplied[:, 3], (-1.0, 1.0))):
            raise ValueError("tangent handedness must be -1 or +1")
        tangent = supplied[:, :3]
        tangent_lengths = np.linalg.norm(tangent, axis=1)
        if (tangent_lengths <= _EPS).any():
            raise ValueError("tangents must be non-zero")
        tangent = tangent / tangent_lengths[:, None]
        bitangent = np.cross(nrm, tangent) * supplied[:, 3, None]
        if uv_transform is not None:
            transform = np.asarray(uv_transform, dtype=np.float64)
            if transform.shape != (2, 2) or not np.isfinite(transform).all():
                raise ValueError("uv_transform must be a finite 2x2 matrix")
            try:
                correction = np.linalg.inv(transform).T
            except np.linalg.LinAlgError as exc:
                raise ValueError("uv_transform must be invertible") from exc
            tangent_bitangent = np.stack((tangent, bitangent), axis=2) @ correction
            tangent = tangent_bitangent[:, :, 0]
            bitangent = tangent_bitangent[:, :, 1]
    else:
        if uv_transform is not None:
            raise ValueError("uv_transform is only valid with supplied tangents; pass mapped uvs otherwise")
        tangent_sum = np.zeros_like(pos)
        bitangent_sum = np.zeros_like(pos)
        for a, b, c in tri:
            edge1, edge2 = pos[b] - pos[a], pos[c] - pos[a]
            duv1, duv2 = tex[b] - tex[a], tex[c] - tex[a]
            denominator = duv1[0] * duv2[1] - duv2[0] * duv1[1]
            if abs(denominator) <= _EPS:
                continue
            tangent = (duv2[1] * edge1 - duv1[1] * edge2) / denominator
            bitangent = (-duv2[0] * edge1 + duv1[0] * edge2) / denominator
            tangent_sum[[a, b, c]] += tangent
            bitangent_sum[[a, b, c]] += bitangent

        tangent = tangent_sum - nrm * np.sum(nrm * tangent_sum, axis=1)[:, None]
        tangent_lengths = np.linalg.norm(tangent, axis=1)
        degenerate = tangent_lengths <= _EPS
        for i in np.flatnonzero(degenerate):
            # Unreferenced vertices and locally degenerate UVs are legal.  Match
            # the writer's deterministic orthogonal fallback instead of failing
            # an otherwise usable primitive.
            axis = np.array((1.0, 0.0, 0.0)) if abs(nrm[i, 0]) < 0.9 else np.array((0.0, 1.0, 0.0))
            tangent[i] = np.cross(nrm[i], axis)
        tangent_lengths = np.linalg.norm(tangent, axis=1)
        tangent /= tangent_lengths[:, None]
        cross_nt = np.cross(nrm, tangent)
        signs = np.sign(np.sum(cross_nt * bitangent_sum, axis=1))
        signs[signs == 0] = 1.0
        bitangent = cross_nt * signs[:, None]

    return _normalise_columns(np.stack((tangent, bitangent, nrm), axis=2), "source frame")


def target_frames(positions, normals, uvs, triangles):
    """Return unsplit per-vertex bases; use target_corner_frames for UV seams."""
    pos, nrm, tex = _vertices(positions, normals, uvs)
    tri = _triangles(triangles, len(pos))
    tangents, bitangents = compute_tangents(pos, nrm, tex, tri, preserve_handedness=True)
    frames = np.stack((np.asarray(tangents), np.asarray(bitangents), nrm), axis=2)
    return _normalise_columns(frames, "target frame")


def target_corner_frames(positions, normals, uvs, triangles, max_vertices=65535):
    """Return ``Fx3x3x3`` writer bases after production mesh splitting.

    Axes are face, corner, world component, and basis column.  A vertex shared
    across opposite UV handedness is duplicated before computing tangents.
    Chunk splits retain that global basis and the input triangle/corner order.
    """
    pos, nrm, tex = _vertices(positions, normals, uvs)
    tri = _triangles(triangles, len(pos))
    mesh = Mesh(positions=[tuple(v) for v in pos], normals=[tuple(v) for v in nrm],
                uvs=[tuple(v) for v in tex], triangles=[tuple(map(int, t)) for t in tri],
                uv_handedness=True)
    corners = []
    for part in split_meshes([mesh], max_vertices=max_vertices):
        tangent, bitangent = tangent_basis(part.tangents, part.normals)
        part_frames = _normalise_columns(
            np.stack((tangent, bitangent, np.asarray(part.normals)), axis=2), "target frame")
        corners.append(part_frames[np.asarray(part.triangles, dtype=np.int64)])
    return np.concatenate(corners, axis=0)


def source_corner_frames(positions, normals, uvs, triangles, tangents=None, uv_transform=None):
    """Return source bases per corner, retaining generated mirrored-UV seams."""
    if tangents is not None:
        frames = source_frames(positions, normals, uvs, triangles, tangents, uv_transform)
        return frames[_triangles(triangles, len(frames))]
    if uv_transform is not None:
        raise ValueError("uv_transform is only valid with supplied tangents; pass mapped uvs otherwise")
    return target_corner_frames(positions, normals, uvs, triangles)


def interpolate_frames(frames, triangles, face_indices, barycentric):
    """Interpolate vertex frames and normalize each column like the sample shader."""
    basis = np.asarray(frames, dtype=np.float64)
    if basis.ndim != 3 or basis.shape[1:] != (3, 3) or len(basis) == 0:
        raise ValueError("frames must be a non-empty Nx3x3 array")
    if not np.isfinite(basis).all():
        raise ValueError("frames must be finite")
    tri = _triangles(triangles, len(basis))
    faces = np.asarray(face_indices)
    weights = np.asarray(barycentric, dtype=np.float64)
    if faces.ndim != 1 or not np.issubdtype(faces.dtype, np.integer):
        raise ValueError("face_indices must be a one-dimensional integer array")
    faces = faces.astype(np.int64, copy=False)
    if weights.shape != (len(faces), 3):
        raise ValueError("barycentric must be an Nx3 array matching face_indices")
    if not np.isfinite(weights).all():
        raise ValueError("barycentric coordinates must be finite")
    if (faces < 0).any() or (faces >= len(tri)).any():
        raise ValueError("face index is out of range")
    if len(faces) == 0:
        return np.empty((0, 3, 3), dtype=np.float64)
    interpolated = np.einsum("pi,pijk->pjk", weights, basis[tri[faces]])
    return _normalise_columns(interpolated, "interpolated frame")


def reorient_normals(samples, source_frames, target_frames, scale=1.0):
    """Re-express sampled glTF tangent normals in a target tangent basis."""
    rgba = np.asarray(samples, dtype=np.float64)
    source = np.asarray(source_frames, dtype=np.float64)
    target = np.asarray(target_frames, dtype=np.float64)
    if rgba.ndim != 2 or rgba.shape[1:] != (4,):
        raise ValueError("samples must be an Nx4 array")
    if source.shape != (len(rgba), 3, 3) or target.shape != source.shape:
        raise ValueError("source_frames and target_frames must be Nx3x3 arrays matching samples")
    if not np.isfinite(rgba).all() or not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("samples and frames must be finite")
    if isinstance(scale, bool) or not np.isscalar(scale) or not np.isfinite(scale):
        raise ValueError("normal scale must be a finite number")
    if ((rgba < 0.0) | (rgba > 1.0)).any():
        raise ValueError("normal samples must be in the [0, 1] range")
    if len(rgba) == 0:
        return rgba.copy()

    normal = rgba[:, :3] * 2.0 - 1.0
    divisor = max(1.0, abs(float(scale)))
    normal[:, :2] *= float(scale) / divisor
    normal[:, 2] /= divisor
    lengths = np.linalg.norm(normal, axis=1)
    if not np.isfinite(lengths).all() or (lengths <= _EPS).any():
        raise ValueError("scaled normal contains a zero or non-finite vector")
    normal /= lengths[:, None]
    world = np.einsum("nij,nj->ni", source, normal)
    try:
        result_normal = np.linalg.solve(target, world[..., None])[..., 0]
    except np.linalg.LinAlgError as exc:
        raise ValueError("target frame is singular") from exc
    lengths = np.linalg.norm(result_normal, axis=1)
    if not np.isfinite(lengths).all() or (lengths <= _EPS).any():
        raise ValueError("reoriented normal is zero or non-finite")
    result_normal /= lengths[:, None]
    result = rgba.copy()
    result[:, :3] = np.clip((result_normal + 1.0) * 0.5, 0.0, 1.0)
    return result

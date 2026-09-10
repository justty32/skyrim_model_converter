"""Optional authored and generated tangent frames for any2nif.

Bitangent = cross(normal, tangent.xyz) * tangent.w, per glTF mesh attributes:
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes-overview
The legacy gltf2nif reader leaves this opt-in disabled.
"""

from dataclasses import replace

import numpy as np

from ._binwriter import GltfError


def prepare_tangent_frames(mesh):
    """Split generated tangent seams and attach a stable tangent VEC4.

    A vertex shared by UV faces of opposite handedness cannot store one valid W.
    Keep the first face group on the original vertex and append at most one copy
    for its opposite group. Authored tangents already carry this decision.
    """
    if not mesh.uv_handedness or mesh.tangents:
        return mesh
    if not mesh.positions:
        return mesh

    from .geometry import compute_tangents, face_normals

    count = len(mesh.positions)
    normals = list(mesh.normals) if mesh.has_normals else face_normals(
        mesh.positions, mesh.triangles)
    have_uv = mesh.has_uvs
    positions = np.asarray(mesh.positions, dtype=np.float64)
    uv = np.asarray(mesh.uvs, dtype=np.float64) if have_uv else None
    nrm = np.asarray(normals, dtype=np.float64)
    normal_lengths = np.linalg.norm(nrm, axis=1)
    nonzero = normal_lengths > 0
    nrm[nonzero] /= normal_lengths[nonzero, None]
    normals = [tuple(value) for value in nrm]

    primary = {}
    duplicates = {}
    source_indices = list(range(count))
    rewritten = []
    for triangle in mesh.triangles:
        a, b, c = triangle
        sign = 1.0
        if have_uv:
            duv1, duv2 = uv[b] - uv[a], uv[c] - uv[a]
            determinant = float(duv1[0] * duv2[1] - duv2[0] * duv1[1])
            face = np.cross(positions[b] - positions[a], positions[c] - positions[a])
            facing = float(np.dot(face, nrm[[a, b, c]].sum(axis=0)))
            if (np.isfinite(determinant) and determinant != 0 and
                    np.isfinite(facing) and facing != 0):
                sign = 1.0 if (determinant > 0) == (facing > 0) else -1.0
        mapped = []
        for index in triangle:
            if index not in primary:
                primary[index] = sign
                mapped.append(index)
            elif primary[index] == sign:
                mapped.append(index)
            else:
                key = (index, sign)
                if key not in duplicates:
                    duplicates[key] = len(source_indices)
                    source_indices.append(index)
                mapped.append(duplicates[key])
        rewritten.append(tuple(mapped))

    def select(values):
        return [values[index] for index in source_indices] if values else []

    prepared_positions = select(mesh.positions)
    prepared_normals = select(normals)
    prepared_uvs = select(mesh.uvs)
    tangent_xyz, _ = compute_tangents(
        prepared_positions, prepared_normals, prepared_uvs, rewritten)
    vertex_signs = [primary.get(index, 1.0) for index in range(count)]
    vertex_signs.extend(sign for (_, sign), _ in sorted(
        duplicates.items(), key=lambda item: item[1]))
    tangents = [(*xyz, sign) for xyz, sign in zip(tangent_xyz, vertex_signs)]
    return replace(
        mesh,
        positions=prepared_positions,
        normals=prepared_normals,
        uvs=prepared_uvs,
        triangles=rewritten,
        colors=select(mesh.colors),
        tangents=tangents,
    )


def normalise_tangents(values, count):
    values = np.asarray(values, dtype=np.float64)
    if values.shape != (count, 4) or not np.isfinite(values).all():
        raise GltfError('TANGENT must contain a finite VEC4 per POSITION')
    if not np.all(np.isin(values[:, 3], (-1.0, 1.0))):
        raise GltfError('TANGENT handedness must be -1 or +1')
    lengths = np.linalg.norm(values[:, :3], axis=1)
    if not np.isfinite(lengths).all() or np.any(lengths <= 1e-12):
        raise GltfError('TANGENT direction must be finite and nonzero')
    result = values.copy()
    result[:, :3] /= lengths[:, None]
    return result


def read_tangents(gltf, buffers, attributes, count, linear):
    from .gltf_reader import _read_accessor

    # glTF requires supplied tangents to be ignored without source normals.
    index = getattr(attributes, 'TANGENT', None)
    if index is None or getattr(attributes, 'NORMAL', None) is None:
        return []
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(gltf.accessors):
        raise GltfError('TANGENT accessor is out of range')
    accessor = gltf.accessors[index]
    if accessor.type != 'VEC4' or accessor.componentType != 5126 or accessor.normalized:
        raise GltfError('TANGENT must use non-normalized FLOAT VEC4')
    values = normalise_tangents(_read_accessor(gltf, buffers, index), count)
    determinant = float(np.linalg.det(linear))
    if not np.isfinite(determinant) or determinant == 0:
        raise GltfError('node transform is singular; cannot transform TANGENT')
    values[:, :3] = values[:, :3] @ linear.T
    values[:, 3] *= -1 if determinant < 0 else 1
    return [tuple(v) for v in normalise_tangents(values, count)]


def tangent_basis(values, normals):
    """Validate and normalize authored directions, retaining their handedness."""
    values = normalise_tangents(values, len(normals))
    tangents = values[:, :3]
    bitangents = np.cross(np.asarray(normals, dtype=np.float64), tangents) * values[:, 3, None]
    lengths = np.linalg.norm(bitangents, axis=1)
    if not np.isfinite(lengths).all() or np.any(lengths <= 1e-12):
        raise GltfError('TANGENT must not be parallel to NORMAL')
    bitangents /= lengths[:, None]
    return [tuple(t) for t in tangents], [tuple(b) for b in bitangents]

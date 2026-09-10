"""Optional authored tangent frames for any2nif's direct texture path.

Bitangent = cross(normal, tangent.xyz) * tangent.w, per glTF mesh attributes:
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes-overview
The legacy gltf2nif reader leaves this opt-in disabled.
"""

import numpy as np

from ._binwriter import GltfError


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

"""Split oversized glTF-space meshes into NIF-safe triangle batches."""

from __future__ import annotations

import math

from gltf2nif.geometry import Mesh

from .errors import AnyError


def _validate_values(label, values, width, count):
    if not values:
        return
    if len(values) != count:
        raise AnyError(f"mesh split: {label} count differs from positions")
    for index, value in enumerate(values):
        try:
            valid = len(value) == width and all(math.isfinite(float(item)) for item in value)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise AnyError(f"mesh split: {label}[{index}] must contain {width} finite numbers")


def _validate(mesh):
    if not isinstance(mesh, Mesh):
        raise AnyError("mesh split: expected gltf2nif.geometry.Mesh input")
    count = len(mesh.positions)
    _validate_values("positions", mesh.positions, 3, count)
    _validate_values("normals", mesh.normals, 3, count)
    _validate_values("uvs", mesh.uvs, 2, count)
    _validate_values("colors", mesh.colors, 4, count)
    _validate_values("tangents", mesh.tangents, 4, count)
    for number, triangle in enumerate(mesh.triangles):
        if not isinstance(triangle, (tuple, list)) or len(triangle) != 3:
            raise AnyError(f"mesh split: triangle {number} must have exactly 3 indices")
        for index in triangle:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
                raise AnyError(f"mesh split: triangle {number} has an invalid vertex index")


def _part(mesh, triangles, number):
    old_to_new = {}
    source_indices = []
    rewritten = []
    for triangle in triangles:
        mapped = []
        for old in triangle:
            if old not in old_to_new:
                old_to_new[old] = len(source_indices)
                source_indices.append(old)
            mapped.append(old_to_new[old])
        rewritten.append(tuple(mapped))

    def select(values):
        return [values[index] for index in source_indices] if values else []

    name = f"{mesh.name}_part_{number:03d}" if mesh.name else f"part_{number:03d}"
    return Mesh(
        name=name,
        positions=select(mesh.positions),
        normals=select(mesh.normals),
        uvs=select(mesh.uvs),
        triangles=rewritten,
        material=mesh.material,
        material_index=mesh.material_index,
        colors=select(mesh.colors),
        tangents=select(mesh.tangents),
    )


def split_meshes(meshes, max_vertices=65535):
    """Return meshes whose triangle-referenced vertex sets fit ``max_vertices``.

    Meshes already within the limit are returned by identity. Oversized meshes are
    greedily batched in source triangle order; indices shared inside a batch remain
    shared. No triangle is dropped or reordered.
    """
    if isinstance(max_vertices, bool) or not isinstance(max_vertices, int) or max_vertices < 3:
        raise AnyError("mesh split: max_vertices must be an integer of at least 3")

    result = []
    for mesh in meshes:
        _validate(mesh)
        if len(mesh.positions) <= max_vertices:
            result.append(mesh)
            continue
        if not mesh.triangles:
            raise AnyError("mesh split: oversized mesh has no triangles")

        batches = []
        current = []
        used = set()
        for triangle in mesh.triangles:
            added = set(triangle) - used
            if current and len(used) + len(added) > max_vertices:
                batches.append(current)
                current = []
                used = set()
                added = set(triangle)
            current.append(triangle)
            used.update(added)
        if current:
            batches.append(current)
        result.extend(_part(mesh, triangles, number)
                      for number, triangles in enumerate(batches, start=1))
    return result

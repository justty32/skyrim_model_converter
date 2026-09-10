"""Generate simple collision from the final, metre/Y-up render geometry."""

from itertools import product

import numpy as np

from gltf2nif.collision import Hull, load_hulls
from gltf2nif.geometry import convex_hull_planes

from .errors import AnyError

AUTO_MODES = ("box", "convex", "convex-mesh")


def _render_vertices(meshes):
    """Return finite, triangle-referenced render vertices in Havok Z-up metres."""
    points = []
    for mesh in meshes:
        try:
            used = sorted({index for triangle in mesh.triangles for index in triangle})
            points.extend(mesh.positions[index] for index in used)
        except (IndexError, TypeError) as exc:
            raise AnyError("automatic collision: triangle index is out of range") from exc
    if not points:
        raise AnyError("automatic collision: model has no triangle vertices")
    vertices = np.asarray(points, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise AnyError("automatic collision: vertices must be finite 3D coordinates")

    # Render meshes are Y-up metres here; bhk vertices are Z-up metres.
    vertices = vertices[:, [0, 2, 1]].copy()
    vertices[:, 1] *= -1
    return np.unique(vertices, axis=0)


def _convex(vertices):
    """Build one enclosing hull, intentionally filling source concavities and holes."""
    try:
        from scipy.spatial import ConvexHull, QhullError
    except ImportError as exc:
        raise AnyError("automatic convex collision requires scipy in the project environment") from exc
    if len(vertices) < 3:
        raise AnyError("automatic collision: convex hull needs at least 3 unique vertices")

    centred = vertices - vertices.mean(axis=0)
    _, singular, vh = np.linalg.svd(centred, full_matrices=False)
    tolerance = max(np.ptp(vertices, axis=0).max(), 1.0) * 1e-10
    rank = int(np.count_nonzero(singular > tolerance))
    if rank < 2:
        raise AnyError("automatic collision: convex hull geometry is degenerate")
    if rank == 2:
        # Havok cannot use a zero-volume hull. Give a flat floor/wall 5 cm total
        # thickness along its fitted normal, matching gltf2nif's JSON convention.
        normal = vh[-1]
        normal /= np.linalg.norm(normal)
        offset = normal * 0.025
        vertices = np.vstack((vertices + offset, vertices - offset))

    try:
        hull = ConvexHull(vertices)
    except QhullError as exc:
        raise AnyError("automatic collision: cannot form a usable convex hull") from exc
    hull_vertices = vertices[hull.vertices]
    planes = [(equation[:3].copy(), float(equation[3])) for equation in hull.equations]
    return Hull(hull_vertices, planes)


def collision_hulls(mode, meshes):
    """Resolve none, automatic hulls, or the existing metre/Y-up JSON input.

    Box bounds cover the entire visible model after node/unit/axis transforms.
    Only triangle-referenced vertices contribute. Thin axes receive 5 cm of
    total thickness, matching the existing flat-hull extrusion convention.
    """
    if not mode or mode == "none":
        return None
    if mode not in AUTO_MODES:
        return load_hulls(mode)

    if mode == "convex-mesh":
        hulls = []
        for index, mesh in enumerate(meshes):
            try:
                hulls.append(_convex(_render_vertices([mesh])))
            except AnyError as exc:
                raise AnyError(
                    f"automatic collision: mesh {index} ({mesh.name!r}): {exc}",
                    code=exc.code,
                ) from exc
        if not hulls:
            raise AnyError("automatic collision: model has no meshes")
        return hulls

    vertices = _render_vertices(meshes)
    if mode == "convex":
        return [_convex(vertices)]

    low, high = vertices.min(axis=0), vertices.max(axis=0)
    padding = np.maximum(0.0, 0.05 - (high - low)) / 2.0
    low, high = low - padding, high + padding
    corners = np.array(list(product(*zip(low, high))), dtype=np.float64)
    planes = convex_hull_planes(corners)
    if not planes:
        raise AnyError("automatic collision: cannot form a usable collision box")
    return [Hull(corners, planes)]

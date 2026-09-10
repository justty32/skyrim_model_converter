"""UV atlas generation and texture-space rasterization helpers.

The xatlas output contract (new-to-old vertex mapping, unchanged index count,
and pixel packing options) is documented at:
https://github.com/mworchel/xatlas-python
https://github.com/jpcy/xatlas/blob/master/source/xatlas/xatlas.h
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_edt


def _array(value, shape_tail, name, dtype=None):
    array = np.asarray(value)
    if array.ndim != 2 or array.shape[1:] != shape_tail:
        raise ValueError(f"{name} must have shape (N, {shape_tail[0]})")
    if not np.issubdtype(array.dtype, np.number) or not np.isfinite(array).all():
        raise ValueError(f"{name} must contain finite numbers")
    result = np.asarray(array, dtype=dtype) if dtype is not None else array
    if not np.isfinite(result).all():
        raise ValueError(f"{name} cannot be represented as finite values")
    return result


def _validate_size(size):
    if isinstance(size, (bool, np.bool_)) or not isinstance(size, (int, np.integer)) or size <= 0:
        raise ValueError("size must be a positive integer")
    return int(size)


def generate_atlas(positions, triangles, normals, size):
    """Create one square UV atlas, returning ``vmapping, triangles, uvs``.

    ``vmapping`` maps each output vertex to an input vertex.  xatlas is
    allowed to split vertices at seams, but not to reorder source corners.
    """
    size = _validate_size(size)
    positions = _array(positions, (3,), "positions", np.float32)
    normals = _array(normals, (3,), "normals", np.float32)
    triangles = _array(triangles, (3,), "triangles")
    if normals.shape != positions.shape:
        raise ValueError("normals must have the same shape as positions")
    if not np.issubdtype(triangles.dtype, np.integer):
        raise ValueError("triangles must contain integer indices")
    if len(positions) == 0 or len(triangles) == 0:
        raise ValueError("atlas requires at least one vertex and triangle")
    if (triangles < 0).any() or (triangles >= len(positions)).any():
        raise ValueError("triangle index is out of range")
    triangles = np.asarray(triangles, dtype=np.uint32)

    import xatlas

    atlas = xatlas.Atlas()
    atlas.add_mesh(positions, triangles, normals=normals)
    options = xatlas.PackOptions()
    # A zero resolution asks xatlas for one dynamically sized atlas. Supplying
    # a resolution can turn a dense mesh into multiple tiles; normalized UVs
    # can be sampled at ``size`` regardless of the native packing dimensions.
    options.resolution = 0
    # Use extra source-atlas padding because the normalized result is resampled
    # into the requested square texture below.
    options.padding = 8
    options.bilinear = True
    options.rotate_charts = True
    atlas.generate(pack_options=options)
    if atlas.width <= 0 or atlas.height <= 0:
        raise ValueError("xatlas returned invalid atlas dimensions")
    vmapping, output_triangles, uvs = atlas[0]
    atlas_indices, _ = atlas.get_mesh_vertex_assignment(0)
    if not np.issubdtype(np.asarray(vmapping).dtype, np.integer):
        raise ValueError("xatlas returned a non-integer vertex mapping")
    if not np.issubdtype(np.asarray(output_triangles).dtype, np.integer):
        raise ValueError("xatlas returned non-integer triangle indices")
    vmapping = np.asarray(vmapping, dtype=np.uint32)
    output_triangles = np.asarray(output_triangles, dtype=np.uint32)
    uvs = np.asarray(uvs, dtype=np.float32)
    if output_triangles.shape != triangles.shape or uvs.shape != (len(vmapping), 2):
        raise ValueError("xatlas returned malformed mesh arrays")
    if len(vmapping) == 0 or (vmapping >= len(positions)).any():
        raise ValueError("xatlas returned an invalid vertex mapping")
    if (output_triangles >= len(vmapping)).any():
        raise ValueError("xatlas returned an out-of-range triangle index")
    # Vertices outside a chart use -1 (UINT32_MAX in this binding), including
    # rare referenced vertices xatlas could not parameterize.
    assigned = np.asarray(atlas_indices)[np.unique(output_triangles)]
    if np.any((assigned != 0) & (assigned != np.iinfo(np.uint32).max) & (assigned != -1)):
        raise ValueError("xatlas returned multiple atlas tiles")
    unassigned = assigned != 0
    if np.any(unassigned):
        count = int(np.count_nonzero(unassigned))
        warnings.warn(
            f"xatlas left {count} referenced vertex/vertices outside its atlas; "
            "their faces may have no texture coverage",
            RuntimeWarning,
            stacklevel=2,
        )
    if not np.isfinite(uvs).all() or (uvs < 0).any() or (uvs > 1).any():
        raise ValueError("xatlas returned UVs outside the atlas")
    if not np.array_equal(vmapping[output_triangles], triangles):
        raise ValueError("xatlas changed source triangle order or geometry")
    # Keep output-sized border space even when one rectangular chart fills the
    # native atlas. Unparameterized tiny islands can then receive their own tile.
    margin = min(8 if size <= 1024 else 16, size // 4)
    uvs = (uvs * (size - 2 * margin) + margin) / size
    return vmapping, output_triangles, uvs


def rasterize(uvs, triangles, size, *, allow_degenerate=False, quantized=False,
              reference_uvs=None, topology=None):
    """Rasterize UV triangles at texel centers, with UV v=0 on the top row."""
    size = _validate_size(size)
    uvs = _array(uvs, (2,), "uvs", np.float64)
    triangles = _array(triangles, (3,), "triangles")
    if not np.issubdtype(triangles.dtype, np.integer):
        raise ValueError("triangles must contain integer indices")
    triangles = np.asarray(triangles, dtype=np.int64)
    if (triangles < 0).any() or (triangles >= len(uvs)).any():
        raise ValueError("triangle index is out of range")
    topology = np.arange(len(uvs), dtype=np.int64) if topology is None else np.asarray(topology)
    if (topology.ndim != 1 or len(topology) != len(uvs) or
            not np.issubdtype(topology.dtype, np.integer)):
        raise ValueError("topology must be an integer vertex mapping matching uvs")
    if quantized:
        if reference_uvs is None:
            raise ValueError("quantized rasterization requires reference_uvs")
        reference_uvs = _array(reference_uvs, (2,), "reference_uvs", np.float64)
        if reference_uvs.shape != uvs.shape:
            raise ValueError("reference_uvs must have the same shape as uvs")
        if not np.array_equal(uvs, reference_uvs.astype(np.float16).astype(np.float64)):
            raise ValueError("quantized UVs must be the half-precision reference UVs")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rasterize(reference_uvs, triangles, size, allow_degenerate=True,
                      topology=topology)

    face_ids = np.full((size, size), -1, dtype=np.int32)
    barycentric = np.zeros((size, size, 3), dtype=np.float32)
    boundary = np.zeros((size, size), dtype=bool)
    empty = []
    eps = 1e-10

    for face, indices in enumerate(triangles):
        points = uvs[indices]
        a, b, c = points
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) <= eps:
            if allow_degenerate:
                empty.append(face)
                continue
            raise ValueError(f"triangle {face} has zero UV area")
        lo = np.ceil(points.min(axis=0) * size - 0.5).astype(np.int64)
        hi = np.floor(points.max(axis=0) * size - 0.5).astype(np.int64)
        lo = np.maximum(lo, 0)
        hi = np.minimum(hi, size - 1)
        if (lo > hi).any():
            empty.append(face)
            continue
        xs = np.arange(lo[0], hi[0] + 1)
        face_covered = False
        for row_start in range(lo[1], hi[1] + 1, 128):
            row_end = min(row_start + 127, hi[1])
            ys = np.arange(row_start, row_end + 1)
            xx, yy = np.meshgrid((xs + 0.5) / size, (ys + 0.5) / size)
            w0 = ((b[1] - c[1]) * (xx - c[0]) + (c[0] - b[0]) * (yy - c[1])) / denominator
            w1 = ((c[1] - a[1]) * (xx - c[0]) + (a[0] - c[0]) * (yy - c[1])) / denominator
            weights = np.stack((w0, w1, 1.0 - w0 - w1), axis=-1)
            covered = np.all(weights >= -eps, axis=-1)
            if not covered.any():
                continue
            face_covered = True
            region_ids = face_ids[row_start:row_end + 1, lo[0]:hi[0] + 1]
            region_boundary = boundary[row_start:row_end + 1, lo[0]:hi[0] + 1]
            on_edge = np.any(weights <= eps, axis=-1)
            # A shared edge may hit the same center. Give it to the first face;
            # two interiors at one center prove a real raster-space overlap.
            conflicts = covered & (region_ids >= 0) & ~on_edge & ~region_boundary
            for other in np.unique(region_ids[conflicts]):
                # Half precision can move a shared edge by a fraction of a texel.
                # The raw atlas preflight bounds permitted overlap near edges.
                if not quantized:
                    current_topology = topology[indices]
                    other_topology = topology[triangles[int(other)]]
                    shared = np.intersect1d(current_topology, other_topology)
                    if len(shared) != 2:
                        raise ValueError(f"UV triangles {int(other)} and {face} overlap")
                    edge_indices = [indices[np.flatnonzero(current_topology == vertex)[0]]
                                    for vertex in shared]
                    edge_start, edge_end = uvs[edge_indices]
                    edge = edge_end - edge_start
                    distance = np.abs(edge[0] * (yy - edge_start[1]) -
                                      edge[1] * (xx - edge_start[0])) / max(np.linalg.norm(edge), eps)
                    if np.any((region_ids == other) & conflicts & (distance > .5 / size)):
                        raise ValueError(f"UV triangles {int(other)} and {face} overlap away from shared edge")
            write = covered & (region_ids < 0)
            region_ids[write] = face
            region_boundary[write] = on_edge[write]
            barycentric[row_start:row_end + 1, lo[0]:hi[0] + 1][write] = weights[write]
        if not face_covered:
            empty.append(face)

    if empty:
        preview = ", ".join(map(str, empty[:5]))
        suffix = ", ..." if len(empty) > 5 else ""
        warnings.warn(
            f"{len(empty)} UV triangle(s) have no covered texel centers "
            f"(first face ids: {preview}{suffix})",
            RuntimeWarning,
            stacklevel=2,
        )
    return face_ids, barycentric


def ensure_face_coverage(vmapping, triangles, raw_uvs, size):
    """Give every face at least one texel without borrowing another chart.

    Faces too small for a texel center (or left unparameterized by xatlas) are
    moved to independent 4x4 or 8x8 fallback tiles. Their corners get private output
    vertices, so moving them never changes an adjacent face's UVs.
    """
    size = _validate_size(size)
    vmapping = np.asarray(vmapping)
    triangles = _array(triangles, (3,), "triangles")
    raw_uvs = _array(raw_uvs, (2,), "raw_uvs", np.float32)
    if vmapping.ndim != 1 or not np.issubdtype(vmapping.dtype, np.integer):
        raise ValueError("vmapping must be a one-dimensional integer array")
    if not np.issubdtype(triangles.dtype, np.integer):
        raise ValueError("triangles must contain integer indices")
    vmapping = np.asarray(vmapping, dtype=np.uint32)
    triangles = np.asarray(triangles, dtype=np.uint32).copy()
    if raw_uvs.shape != (len(vmapping), 2):
        raise ValueError("raw_uvs must match vmapping")
    if (triangles >= len(vmapping)).any():
        raise ValueError("triangle index is out of range")

    half_uvs = raw_uvs.astype(np.float16).astype(np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        owners, barycentric = rasterize(
            half_uvs, triangles, size, allow_degenerate=True, quantized=True,
            reference_uvs=raw_uvs, topology=vmapping,
        )
    present = np.zeros(len(triangles), dtype=bool)
    present[np.unique(owners[owners >= 0])] = True
    missing = np.flatnonzero(~present)
    if not len(missing):
        return vmapping, triangles, half_uvs, owners, barycentric

    # Four texels are sufficient through 1024; at higher resolutions float16
    # UV spacing itself can approach two texels near 1.0, so use eight.
    tile = 8 if size > 1024 else 4
    # Reserve one texel around all existing coverage. Aligned tiles make the
    # search O(H*W), independent of the number of missing faces.
    # Reserve the complete bounding boxes of faces that stay in the atlas, not
    # merely their covered centers. A very thin face can cross an otherwise
    # empty tile without owning its center.
    difference = np.zeros((size + 1, size + 1), dtype=np.int32)
    for face in np.flatnonzero(present):
        points = raw_uvs[triangles[face]] * size
        x0, y0 = np.maximum(np.floor(points.min(axis=0)).astype(int), 0)
        x1, y1 = np.minimum(np.ceil(points.max(axis=0)).astype(int), size - 1)
        difference[y0, x0] += 1
        difference[y1 + 1, x0] -= 1
        difference[y0, x1 + 1] -= 1
        difference[y1 + 1, x1 + 1] += 1
    boxes = difference[:-1, :-1].cumsum(axis=0).cumsum(axis=1) > 0
    unavailable = binary_dilation(boxes | (owners >= 0), iterations=1)
    grid = size // tile
    if grid == 0:
        candidates = np.empty((0, 2), dtype=np.int64)
    else:
        usable = unavailable[:grid * tile, :grid * tile]
        blocks = usable.reshape(grid, tile, grid, tile)
        free = ~blocks.any(axis=(1, 3))
        candidates = np.argwhere(free)
    if len(candidates) < len(missing):
        raise ValueError(
            f"UV atlas has {len(missing)} face(s) without texel coverage and only "
            f"{len(candidates)} safe fallback tile(s); increase --bake-size"
        )

    new_mapping = [vmapping]
    new_raw_uvs = [raw_uvs]
    first = len(vmapping)
    for face, (block_y, block_x) in zip(missing, candidates):
        source_corners = vmapping[triangles[face]]
        triangles[face] = np.arange(first, first + 3, dtype=np.uint32)
        x, y = int(block_x) * tile, int(block_y) * tile
        fallback = np.array(((x + 1, y + 1), (x + tile - 1, y + 1),
                             (x + 1, y + tile - 1)), dtype=np.float32) / size
        new_mapping.append(source_corners)
        new_raw_uvs.append(fallback)
        first += 3

    vmapping = np.concatenate(new_mapping).astype(np.uint32, copy=False)
    raw_uvs = np.concatenate(new_raw_uvs).astype(np.float32, copy=False)
    half_uvs = raw_uvs.astype(np.float16).astype(np.float32)
    owners, barycentric = rasterize(
        half_uvs, triangles, size, allow_degenerate=True, quantized=True,
        reference_uvs=raw_uvs, topology=vmapping,
    )
    present = np.zeros(len(triangles), dtype=bool)
    present[np.unique(owners[owners >= 0])] = True
    still_missing = np.flatnonzero(~present)
    if len(still_missing):
        preview = ", ".join(map(str, still_missing[:5]))
        raise ValueError(
            f"UV fallback failed to cover {len(still_missing)} face(s) "
            f"(first face ids: {preview}); increase --bake-size"
        )
    warnings.warn(
        f"placed {len(missing)} face(s) without texel coverage in independent fallback tiles",
        RuntimeWarning,
        stacklevel=2,
    )
    return vmapping, triangles, half_uvs, owners, barycentric


def dilate_pixels(pixels, face_ids, padding=4):
    """Copy the nearest covered texel into empty texels within ``padding``."""
    pixels = np.asarray(pixels)
    face_ids = np.asarray(face_ids)
    if pixels.ndim < 2 or face_ids.shape != pixels.shape[:2]:
        raise ValueError("face_ids must match the first two pixel dimensions")
    if not np.issubdtype(face_ids.dtype, np.integer):
        raise ValueError("face_ids must contain integers")
    if isinstance(padding, (bool, np.bool_)) or not isinstance(padding, (int, np.integer)) or padding < 0:
        raise ValueError("padding must be a nonnegative integer")
    result = pixels.copy()
    covered = face_ids >= 0
    if padding == 0 or not covered.any() or covered.all():
        return result
    distances, nearest = distance_transform_edt(~covered, return_indices=True)
    fill = (~covered) & (distances <= padding)
    result[fill] = pixels[nearest[0][fill], nearest[1][fill]]
    return result

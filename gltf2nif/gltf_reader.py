"""Read a glTF 2.0 file -> list[Mesh] (glTF space, Y-up metres), plus texture probing.

Each glTF mesh primitive (triangles only) becomes one Mesh. Interleaved vertex
buffers (byteStride) and non-zero accessor byteOffsets are both handled, so it
reads what real exporters (SharpGLTF / the DarkSouls extractor) emit. The material
NAME (extension stripped) carries the texture base name — nif_writer turns that into
the diffuse/normal .dds slot paths.
"""

from __future__ import annotations

import base64
import os
import struct

import numpy as np
from pygltflib import GLTF2

from ._binwriter import GltfError
from .geometry import Mesh

_COMPONENT = {
    5120: ("<b", 1), 5121: ("<B", 1), 5122: ("<h", 2),
    5123: ("<H", 2), 5125: ("<I", 4), 5126: ("<f", 4),
}
_NUMCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_TRIANGLES = 4  # glTF primitive.mode default


def _normalise_component(value: int, component_type: int) -> float:
    if component_type == 5120:
        return max(float(value) / 127.0, -1.0)
    if component_type == 5121:
        return float(value) / 255.0
    if component_type == 5122:
        return max(float(value) / 32767.0, -1.0)
    if component_type == 5123:
        return float(value) / 65535.0
    raise GltfError("normalized accessor must use BYTE or SHORT components")


def _buffer_bytes(gltf: GLTF2, gltf_dir: str) -> list[bytes]:
    out = []
    for buf in gltf.buffers:
        uri = buf.uri
        if uri is None:  # .glb embedded blob
            out.append(gltf.binary_blob() or b"")
        elif uri.startswith("data:"):
            out.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            with open(os.path.join(gltf_dir, uri), "rb") as fh:
                out.append(fh.read())
    return out


def _read_accessor(gltf: GLTF2, buffers: list[bytes], idx: int):
    if idx < 0 or idx >= len(gltf.accessors):
        raise GltfError(f"accessor index {idx} is out of range")
    acc = gltf.accessors[idx]
    if acc.sparse is not None:
        raise GltfError("sparse accessors are not supported")
    if acc.bufferView is None or acc.bufferView < 0 or acc.bufferView >= len(gltf.bufferViews):
        raise GltfError(f"accessor {idx} has no valid bufferView")
    try:
        fmt, comp_size = _COMPONENT[acc.componentType]
        ncomp = _NUMCOMP[acc.type]
    except KeyError as exc:
        raise GltfError(f"accessor {idx} has unsupported component/type") from exc
    bv = gltf.bufferViews[acc.bufferView]
    if bv.buffer < 0 or bv.buffer >= len(buffers):
        raise GltfError(f"accessor {idx} references an invalid buffer")
    blob = buffers[bv.buffer]
    base = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    stride = bv.byteStride or (comp_size * ncomp)
    packed_size = comp_size * ncomp
    if stride < packed_size:
        raise GltfError(f"accessor {idx} byteStride is too small")
    view_start = bv.byteOffset or 0
    view_end = view_start + bv.byteLength
    if base < view_start or (acc.count and base + (acc.count - 1) * stride + packed_size > view_end):
        raise GltfError(f"accessor {idx} exceeds its bufferView")
    out = []
    for e in range(acc.count):
        off = base + e * stride
        try:
            vals = struct.unpack_from("<" + fmt[1] * ncomp, blob, off)
        except struct.error as exc:
            raise GltfError(f"accessor {idx} exceeds its buffer") from exc
        if acc.normalized:
            vals = tuple(_normalise_component(v, acc.componentType) for v in vals)
        out.append(vals if ncomp > 1 else vals[0])
    return out


def _node_matrix(node) -> np.ndarray:
    if node.matrix:
        if len(node.matrix) != 16:
            raise GltfError("node matrix must have 16 values")
        return np.asarray(node.matrix, dtype=np.float64).reshape((4, 4), order="F")

    translation = np.asarray(node.translation or (0.0, 0.0, 0.0), dtype=np.float64)
    scale = np.asarray(node.scale or (1.0, 1.0, 1.0), dtype=np.float64)
    rotation = np.asarray(node.rotation or (0.0, 0.0, 0.0, 1.0), dtype=np.float64)
    if translation.shape != (3,) or scale.shape != (3,) or rotation.shape != (4,):
        raise GltfError("node TRS has an invalid length")
    norm = float(np.linalg.norm(rotation))
    if norm < 1e-12:
        raise GltfError("node rotation quaternion has zero length")
    x, y, z, w = rotation / norm
    rotation_matrix = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1],
    ], dtype=np.float64)
    trs = np.diag((scale[0], scale[1], scale[2], 1.0))
    trs[:3, 3] = translation
    trs[:3, :3] = rotation_matrix[:3, :3] @ np.diag(scale)
    return trs


def _scene_instances(gltf: GLTF2):
    """Yield (node, mesh index, world matrix) for the active scene."""
    if not gltf.scenes:
        raise GltfError("glTF has no scene to instantiate meshes")
    scene_index = gltf.scene if gltf.scene is not None else 0
    if scene_index < 0 or scene_index >= len(gltf.scenes):
        raise GltfError("default scene index is out of range")

    def walk(node_index: int, parent: np.ndarray, ancestors: frozenset[int]):
        if node_index < 0 or node_index >= len(gltf.nodes):
            raise GltfError(f"node index {node_index} is out of range")
        if node_index in ancestors:
            raise GltfError("node hierarchy contains a cycle")
        node = gltf.nodes[node_index]
        if node.skin is not None:
            raise GltfError(f"node {node_index} uses skinning; only static meshes are supported")
        world = parent @ _node_matrix(node)
        if node.mesh is not None:
            if node.mesh < 0 or node.mesh >= len(gltf.meshes):
                raise GltfError(f"node {node_index} mesh index is out of range")
            yield node, node.mesh, world
        child_ancestors = ancestors | {node_index}
        for child in node.children or ():
            yield from walk(child, world, child_ancestors)

    for root in gltf.scenes[scene_index].nodes or ():
        yield from walk(root, np.eye(4), frozenset())


def _transform_geometry(positions, normals, matrix: np.ndarray):
    linear = matrix[:3, :3]
    points = []
    for position in positions:
        transformed = matrix @ np.array((*position, 1.0), dtype=np.float64)
        points.append(tuple(map(float, transformed[:3])))
    if not normals:
        return points, []
    try:
        normal_matrix = np.linalg.inv(linear).T
    except np.linalg.LinAlgError as exc:
        raise GltfError("node transform is singular; cannot transform normals") from exc
    directions = []
    for normal in normals:
        transformed = normal_matrix @ np.asarray(normal, dtype=np.float64)
        length = float(np.linalg.norm(transformed))
        if length < 1e-12:
            raise GltfError("node transform produced a zero-length normal")
        directions.append(tuple(map(float, transformed / length)))
    return points, directions


def _material_index(gltf: GLTF2, prim) -> int:
    """Primitive's glTF material index, or -1 when absent/out of range."""
    materials = gltf.materials or []
    if prim.material is None or prim.material < 0 or prim.material >= len(materials):
        return -1
    return int(prim.material)


def _material_basename(gltf: GLTF2, prim) -> str:
    if prim.material is None or prim.material < 0 or prim.material >= len(gltf.materials):
        return ""
    name = gltf.materials[prim.material].name or ""
    # Extractor records the texture base name as the material name, sometimes with a
    # source extension (.tga/.dds/.png). Strip it -> the diffuse base, e.g. "m18_wall_07".
    return os.path.splitext(name)[0]


def read_gltf(path: str) -> list[Mesh]:
    """Parse a .gltf/.glb into Mesh IR. Raises GltfError on unusable input."""
    try:
        gltf = GLTF2().load(path)
    except Exception as exc:  # noqa: BLE001
        raise GltfError(f"cannot parse glTF: {exc}") from exc
    if gltf is None or not gltf.meshes:
        raise GltfError("glTF has no meshes")
    if gltf.animations:
        raise GltfError("animated glTF is not supported; only static meshes are supported")
    gltf_dir = os.path.dirname(os.path.abspath(path))
    try:
        buffers = _buffer_bytes(gltf, gltf_dir)
    except Exception as exc:  # noqa: BLE001 - external buffer failures are parse failures
        raise GltfError(f"cannot read glTF buffer: {exc}") from exc

    meshes: list[Mesh] = []
    for node, mi, world in _scene_instances(gltf):
        gmesh = gltf.meshes[mi]
        for pi, prim in enumerate(gmesh.primitives):
            if prim.targets:
                raise GltfError(f"mesh {mi} primitive {pi} uses morph targets; only static meshes "
                                "are supported")
            mode = _TRIANGLES if prim.mode is None else prim.mode
            if mode != _TRIANGLES:
                continue  # only triangle lists
            attrs = prim.attributes
            if attrs.POSITION is None:
                continue
            attribute_indices = [attrs.POSITION]
            if attrs.NORMAL is not None:
                attribute_indices.append(attrs.NORMAL)
            if attrs.TEXCOORD_0 is not None:
                attribute_indices.append(attrs.TEXCOORD_0)
            color_index = getattr(attrs, "COLOR_0", None)
            if color_index is not None:
                attribute_indices.append(color_index)
            if any(index < 0 or index >= len(gltf.accessors) for index in attribute_indices):
                raise GltfError(f"mesh {mi} primitive {pi} accessor is out of range")
            position_accessor = gltf.accessors[attrs.POSITION]
            if (position_accessor.type != "VEC3" or position_accessor.componentType != 5126 or
                    position_accessor.normalized):
                raise GltfError(f"mesh {mi} primitive {pi} POSITION must be non-normalized FLOAT VEC3")
            positions = [tuple(map(float, p)) for p in _read_accessor(gltf, buffers, attrs.POSITION)]
            normals = []
            if attrs.NORMAL is not None:
                normal_accessor = gltf.accessors[attrs.NORMAL]
                if (normal_accessor.type != "VEC3" or normal_accessor.componentType != 5126 or
                        normal_accessor.normalized):
                    raise GltfError(f"mesh {mi} primitive {pi} NORMAL must be non-normalized "
                                    "FLOAT VEC3")
                normals = [tuple(map(float, n)) for n in
                           _read_accessor(gltf, buffers, attrs.NORMAL)]
                if len(normals) != len(positions):
                    raise GltfError(f"mesh {mi} primitive {pi} NORMAL count differs from POSITION")
            uvs = []
            if attrs.TEXCOORD_0 is not None:
                uv_accessor = gltf.accessors[attrs.TEXCOORD_0]
                valid_uv = (uv_accessor.type == "VEC2" and (
                    (uv_accessor.componentType == 5126 and not uv_accessor.normalized) or
                    (uv_accessor.componentType in (5121, 5123) and uv_accessor.normalized)
                ))
                if not valid_uv:
                    raise GltfError(f"mesh {mi} primitive {pi} TEXCOORD_0 has invalid components")
                uvs = [tuple(map(float, t)) for t in
                       _read_accessor(gltf, buffers, attrs.TEXCOORD_0)]
                if len(uvs) != len(positions):
                    raise GltfError(f"mesh {mi} primitive {pi} TEXCOORD_0 count differs from POSITION")
            colors = []
            if color_index is not None:
                color_accessor = gltf.accessors[color_index]
                valid_color = (
                    color_accessor.type in ("VEC3", "VEC4")
                    and (
                        (color_accessor.componentType == 5126 and
                         not color_accessor.normalized)
                        or (color_accessor.componentType in (5121, 5123) and
                            color_accessor.normalized)
                    )
                )
                if not valid_color:
                    raise GltfError(
                        f"mesh {mi} primitive {pi} COLOR_0 has invalid components"
                    )
                raw_colors = _read_accessor(gltf, buffers, color_index)
                colors = [tuple(map(float, color)) + ((1.0,) if len(color) == 3 else ())
                          for color in raw_colors]
                if len(colors) != len(positions):
                    raise GltfError(
                        f"mesh {mi} primitive {pi} COLOR_0 count differs from POSITION"
                    )
            if prim.indices is not None:
                if prim.indices < 0 or prim.indices >= len(gltf.accessors):
                    raise GltfError(f"mesh {mi} primitive {pi} indices accessor is out of range")
                index_accessor = gltf.accessors[prim.indices]
                if (index_accessor.normalized or index_accessor.type != "SCALAR" or
                        index_accessor.componentType not in (5121, 5123, 5125)):
                    raise GltfError(f"mesh {mi} primitive {pi} has invalid indices accessor")
                flat = [int(i) for i in _read_accessor(gltf, buffers, prim.indices)]
            else:
                flat = list(range(len(positions)))
            if len(flat) % 3:
                raise GltfError(f"mesh {mi} primitive {pi} index count is not divisible by 3")
            if any(index < 0 or index >= len(positions) for index in flat):
                raise GltfError(f"mesh {mi} primitive {pi} has an out-of-range index")
            tris = [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]
            if not positions or not tris:
                continue
            positions, normals = _transform_geometry(positions, normals, world)
            if float(np.linalg.det(world[:3, :3])) < 0:
                tris = [(a, c, b) for a, b, c in tris]
            node_name = node.name if node is not None else ""
            meshes.append(Mesh(
                name=node_name or gmesh.name or f"mesh_{mi}_{pi}",
                positions=positions, normals=normals, uvs=uvs, colors=colors,
                triangles=tris,
                material=_material_basename(gltf, prim),
                material_index=_material_index(gltf, prim),
            ))
    if not meshes:
        raise GltfError("glTF has no triangle geometry")
    return meshes


def probe_normal_map(gltf_dir: str, base: str) -> bool:
    """Does a sibling <base>_n.dds exist next to the glTF? Governs whether the
    normal-map texture slot is filled (DSR '_s' spec maps are ignored for now)."""
    if not base:
        return False
    return os.path.isfile(os.path.join(gltf_dir, base + "_n.dds"))

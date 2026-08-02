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
    acc = gltf.accessors[idx]
    fmt, comp_size = _COMPONENT[acc.componentType]
    ncomp = _NUMCOMP[acc.type]
    bv = gltf.bufferViews[acc.bufferView]
    blob = buffers[bv.buffer]
    base = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    stride = bv.byteStride or (comp_size * ncomp)
    out = []
    for e in range(acc.count):
        off = base + e * stride
        vals = struct.unpack_from("<" + fmt[1] * ncomp, blob, off)
        out.append(vals if ncomp > 1 else vals[0])
    return out


def _material_basename(gltf: GLTF2, prim) -> str:
    if prim.material is None or prim.material >= len(gltf.materials):
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
    gltf_dir = os.path.dirname(os.path.abspath(path))
    buffers = _buffer_bytes(gltf, gltf_dir)

    meshes: list[Mesh] = []
    for mi, gmesh in enumerate(gltf.meshes):
        for pi, prim in enumerate(gmesh.primitives):
            if (prim.mode or _TRIANGLES) != _TRIANGLES:
                continue  # only triangle lists
            attrs = prim.attributes
            if attrs.POSITION is None:
                continue
            positions = [tuple(map(float, p)) for p in _read_accessor(gltf, buffers, attrs.POSITION)]
            normals = ([tuple(map(float, n)) for n in _read_accessor(gltf, buffers, attrs.NORMAL)]
                       if attrs.NORMAL is not None else [])
            uvs = ([tuple(map(float, t)) for t in _read_accessor(gltf, buffers, attrs.TEXCOORD_0)]
                   if attrs.TEXCOORD_0 is not None else [])
            if prim.indices is not None:
                flat = [int(i) for i in _read_accessor(gltf, buffers, prim.indices)]
            else:
                flat = list(range(len(positions)))
            tris = [tuple(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]
            if not positions or not tris:
                continue
            meshes.append(Mesh(
                name=gmesh.name or f"mesh_{mi}_{pi}",
                positions=positions, normals=normals, uvs=uvs, triangles=tris,
                material=_material_basename(gltf, prim),
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

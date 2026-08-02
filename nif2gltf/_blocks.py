"""Per-block NIF decoders: given a positioned `_Reader`, parse one block.

Split out of nif_reader.py (behavior-preserving). Each `_read_*` consumes one
block (NiNode-derived group, LE shape/data, or SSE BSTriShape) and returns a
plain dict the top-level parser (read_nif) stitches into the node hierarchy.
Layouts are verbatim from niftools/nifxml nif.xml.
"""

from __future__ import annotations

import struct

import numpy as np

from ._binreader import _Reader


def _local_matrix(trans, rot: np.ndarray, scale: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    m[:3, :3] = rot * scale
    m[:3, 3] = trans
    return m


def _skip_niobjectnet(r: _Reader) -> None:
    r.u32()  # Name (string index)
    num_extra = r.u32()
    for _ in range(num_extra):
        r.i32()  # Extra Data refs
    r.i32()  # Controller ref


def _read_niavobject(r: _Reader):
    """Read NiObjectNET + NiAVObject prefix; return (trans, rot, scale)."""
    _skip_niobjectnet(r)
    r.u32()  # Flags (uint for BSVER > 26)
    trans = r.vec3()
    rot = r.mat33()
    scale = r.f32()
    r.i32()  # Collision Object ref
    return trans, rot, scale


def _read_ninode(r: _Reader) -> dict:
    trans, rot, scale = _read_niavobject(r)
    num_children = r.u32()
    children = [r.i32() for _ in range(num_children)]
    return {"kind": "node", "local": _local_matrix(trans, rot, scale), "children": children}


def _read_le_shape(r: _Reader) -> dict:
    trans, rot, scale = _read_niavobject(r)
    data_ref = r.i32()       # NiGeometry.Data
    skin_ref = r.i32()       # NiGeometry.Skin Instance
    return {
        "kind": "le_shape",
        "local": _local_matrix(trans, rot, scale),
        "data_ref": data_ref,
        "skinned": skin_ref != -1,
    }


def _read_nitrishapedata(r: _Reader) -> dict:
    r.i32()                       # Group ID
    num_vertices = r.u16()
    r.u8()                        # Keep Flags
    r.u8()                        # Compress Flags
    has_vertices = r.u8()
    verts = []
    if has_vertices:
        for _ in range(num_vertices):
            verts.append(r.vec3())
    bs_data_flags = r.u16()       # BS Data Flags (BS202)
    r.u32()                       # Material CRC
    has_normals = r.u8()
    normals = []
    if has_normals:
        for _ in range(num_vertices):
            normals.append(r.vec3())
        if bs_data_flags & 0x1000:  # has tangents
            for _ in range(num_vertices * 2):  # tangents + bitangents
                r.vec3()
    r.vec3(); r.f32()             # Bounding Sphere (NiBound)
    has_vertex_colors = r.u8()
    if has_vertex_colors:
        for _ in range(num_vertices):
            r.take(16)            # Color4
    num_uv_sets = bs_data_flags & 0x1
    uvs = []
    for s in range(num_uv_sets):
        for v in range(num_vertices):
            uv = struct.unpack_from("<2f", r.take(8))
            if s == 0:
                uvs.append(uv)
    r.u16()                       # Consistency Flags
    r.i32()                       # Additional Data ref
    num_triangles = r.u16()       # NiTriBasedGeomData
    r.u32()                       # Num Triangle Points
    has_triangles = r.u8()
    tris = []
    if has_triangles:
        for _ in range(num_triangles):
            tris.append(struct.unpack_from("<3H", r.take(6)))
    return {
        "kind": "data",
        "verts": verts,
        "normals": normals,
        "uvs": uvs,
        "tris": tris,
    }


def _read_nitristripsdata(r: _Reader) -> dict:
    r.i32()                       # Group ID
    num_vertices = r.u16()
    r.u8(); r.u8()                # Keep / Compress Flags
    has_vertices = r.u8()
    verts = []
    if has_vertices:
        for _ in range(num_vertices):
            verts.append(r.vec3())
    bs_data_flags = r.u16()
    r.u32()                       # Material CRC
    has_normals = r.u8()
    normals = []
    if has_normals:
        for _ in range(num_vertices):
            normals.append(r.vec3())
        if bs_data_flags & 0x1000:
            for _ in range(num_vertices * 2):
                r.vec3()
    r.vec3(); r.f32()             # Bounding Sphere
    has_vertex_colors = r.u8()
    if has_vertex_colors:
        for _ in range(num_vertices):
            r.take(16)
    num_uv_sets = bs_data_flags & 0x1
    uvs = []
    for s in range(num_uv_sets):
        for v in range(num_vertices):
            uv = struct.unpack_from("<2f", r.take(8))
            if s == 0:
                uvs.append(uv)
    r.u16()                       # Consistency Flags
    r.i32()                       # Additional Data ref
    r.u16()                       # Num Triangles (NiTriBasedGeomData)
    num_strips = r.u16()
    strip_lengths = [r.u16() for _ in range(num_strips)]
    has_points = r.u8()
    tris = []
    if has_points:
        for length in strip_lengths:
            strip = [r.u16() for _ in range(length)]
            tris.extend(_destrip(strip))
    return {"kind": "data", "verts": verts, "normals": normals, "uvs": uvs, "tris": tris}


def _destrip(strip: list[int]) -> list[tuple[int, int, int]]:
    out = []
    for i in range(len(strip) - 2):
        a, b, c = strip[i], strip[i + 1], strip[i + 2]
        if a == b or b == c or a == c:
            continue  # degenerate (strip restart)
        if i % 2 == 0:
            out.append((a, b, c))
        else:
            out.append((a, c, b))
    return out


def _decode_half3(buf: bytes, off: int) -> tuple[float, float, float]:
    return tuple(np.frombuffer(buf, dtype="<f2", count=3, offset=off).astype(np.float64))


def _decode_float3(buf: bytes, off: int) -> tuple[float, float, float]:
    return struct.unpack_from("<3f", buf, off)


def _decode_half2(buf: bytes, off: int) -> tuple[float, float]:
    return tuple(np.frombuffer(buf, dtype="<f2", count=2, offset=off).astype(np.float64))


def _read_bstrishape(r: _Reader, bs_version: int) -> dict:
    """SSE BSTriShape via BSVertexDesc offset table (self-describing decode)."""
    trans, rot, scale = _read_niavobject(r)
    r.vec3(); r.f32()             # Bounding Sphere
    if bs_version >= 155:         # F76 Bound Min Max
        for _ in range(6):
            r.f32()
    skin_ref = r.i32()
    shader_ref = r.i32()          # Shader Property (-> BSLightingShaderProperty)
    r.i32()                       # Alpha Property
    vertex_desc = r.u64()
    if bs_version >= 130:         # FO4+: uint triangle count
        num_triangles = r.u32()
    else:                         # SSE: ushort
        num_triangles = r.u16()
    num_vertices = r.u16()
    data_size = r.u32()

    stride = (vertex_desc & 0xF) * 4
    attributes = (vertex_desc >> 44) & 0xFFF
    has_vertex = attributes & 0x1
    has_uv = attributes & 0x2
    has_normal = attributes & 0x8
    skinned = bool(attributes & 0x40) or skin_ref != -1
    uv_off = ((vertex_desc >> 8) & 0xF) * 4
    nrm_off = ((vertex_desc >> 16) & 0xF) * 4
    # Position is float3 (full precision) or half3 — SSE's Full_Precision flag bit is unreliable
    # across exporters (real vanilla rocks decode as float3 with the bit unset), so infer from the
    # byte budget before the first following attribute: float3+bitangentX = 16 B → UV@16 / Normal@20,
    # whereas half3+bitangentX = 8 B → UV@8 / Normal@12. A first offset ≥ 12 can only be float3.
    _attr_offs = [o for o in (uv_off if has_uv else 0, nrm_off if has_normal else 0) if o > 0]
    full_precision = (min(_attr_offs) if _attr_offs else stride) >= 12

    verts: list = []
    normals: list = []
    uvs: list = []
    tris: list = []
    if data_size > 0 and stride > 0:
        vbytes = r.take(stride * num_vertices)
        for v in range(num_vertices):
            base = v * stride
            if has_vertex:
                if full_precision:
                    verts.append(_decode_float3(vbytes, base))
                else:
                    verts.append(_decode_half3(vbytes, base))
            if has_uv:
                uvs.append(_decode_half2(vbytes, base + uv_off))
            if has_normal:
                nx = vbytes[base + nrm_off] / 255.0 * 2.0 - 1.0
                ny = vbytes[base + nrm_off + 1] / 255.0 * 2.0 - 1.0
                nz = vbytes[base + nrm_off + 2] / 255.0 * 2.0 - 1.0
                normals.append((nx, ny, nz))
        for _ in range(num_triangles):
            tris.append(struct.unpack_from("<3H", r.take(6)))

    return {
        "kind": "sse_shape",
        "local": _local_matrix(trans, rot, scale),
        "skinned": skinned,
        "shader_ref": shader_ref,
        "verts": verts,
        "normals": normals if len(normals) == len(verts) else [],
        "uvs": uvs if len(uvs) == len(verts) else [],
        "tris": tris,
    }


# BSLightingShaderProperty: we only need its Texture Set ref. For Skyrim SSE (stream 100) the
# ref sits at a fixed byte offset 40 from the block start (verified against real vanilla nifs —
# rock + multi-shape tree). Block-size seeks recover if a future variant shifts it.
_LSP_TEXSET_OFFSET = 40


def _read_bslightingshaderproperty(r: _Reader, block_start: int) -> dict:
    r.seek(block_start + _LSP_TEXSET_OFFSET)
    return {"kind": "shader", "texset_ref": r.i32()}


def _read_bsshadertextureset(r: _Reader) -> dict:
    n = r.u32()
    paths = [r.sized_string() for _ in range(n)]
    return {"kind": "texset", "diffuse": paths[0] if paths else ""}

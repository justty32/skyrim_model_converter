"""Builders for minimal synthetic Skyrim NIFs (LE + SSE).

These construct valid 20.2.0.7 / user-version-12 byte streams matching the layouts
in nif.xml, so the reader can be round-trip tested offline. They do NOT prove
correctness against *real vanilla* NIFs (that gate is deferred to the main machine);
they prove the reader reads back exactly what the documented layout encodes.
"""

from __future__ import annotations

import struct

import numpy as np

BS_LE = 83
BS_SSE = 100


def _u8(v): return struct.pack("<B", v)
def _u16(v): return struct.pack("<H", v)
def _u32(v): return struct.pack("<I", v)
def _i32(v): return struct.pack("<i", v)
def _u64(v): return struct.pack("<Q", v)
def _f32(v): return struct.pack("<f", v)
def _vec3(t): return struct.pack("<3f", *t)
def _mat33_identity(): return struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1)
def _half(vals): return np.asarray(vals, dtype="<f2").tobytes()
def _sized(s): return _u32(len(s)) + s.encode("latin-1")
def _export(s): return _u8(len(s) + 1) + s.encode("latin-1") + b"\x00"


def _normbyte(n: float) -> int:
    return max(0, min(255, round((n + 1.0) / 2.0 * 255.0)))


def _niobjectnet(name_idx=0):
    return _u32(name_idx) + _u32(0) + _i32(-1)  # Name, NumExtra=0, Controller=-1


def _niavobject(translation=(0.0, 0.0, 0.0), scale=1.0):
    return (
        _niobjectnet()
        + _u32(0x0000000E)            # Flags
        + _vec3(translation)
        + _mat33_identity()
        + _f32(scale)
        + _i32(-1)                    # Collision Object
    )


def _ninode_block(children, translation=(0.0, 0.0, 0.0)):
    b = _niavobject(translation)
    b += _u32(len(children)) + b"".join(_i32(c) for c in children)
    b += _u32(0)                      # Num Effects
    return b


def _nitrishape_block(data_ref, translation=(0.0, 0.0, 0.0)):
    b = _niavobject(translation)
    b += _i32(data_ref)              # Data
    b += _i32(-1)                    # Skin Instance
    # MaterialData (20.2.0.7): NumMaterials, ActiveMaterial, MaterialNeedsUpdate
    b += _u32(0) + _i32(-1) + _u8(0)
    b += _i32(-1)                    # Shader Property
    b += _i32(-1)                    # Alpha Property
    return b


def _nitrishapedata_block(verts, normals, uvs, tris):
    n = len(verts)
    has_uv = 1 if uvs else 0
    flags = has_uv  # bit0 = 1 uv set, no tangents (0x1000 off)
    b = _i32(0)                       # Group ID
    b += _u16(n)
    b += _u8(0) + _u8(0)             # Keep / Compress
    b += _u8(1) + b"".join(_vec3(v) for v in verts)
    b += _u16(flags)                 # BS Data Flags
    b += _u32(0)                     # Material CRC
    if normals:
        b += _u8(1) + b"".join(_vec3(nv) for nv in normals)
    else:
        b += _u8(0)
    b += _vec3((0, 0, 0)) + _f32(0)  # Bounding Sphere
    b += _u8(0)                      # Has Vertex Colors
    if has_uv:
        b += b"".join(struct.pack("<2f", *uv) for uv in uvs)
    b += _u16(0)                     # Consistency Flags
    b += _i32(-1)                    # Additional Data
    b += _u16(len(tris))             # Num Triangles
    b += _u32(len(tris) * 3)         # Num Triangle Points
    b += _u8(1) + b"".join(struct.pack("<3H", *t) for t in tris)
    b += _u16(0)                     # Num Match Groups
    return b


def _header(block_type_names, block_type_index, blocks, strings, bs_version):
    p = [b"Gamebryo File Format, Version 20.2.0.7\n"]
    p.append(_u32(0x14020007))
    p.append(_u8(1))                 # endian little
    p.append(_u32(12))               # user version
    p.append(_u32(len(blocks)))
    p.append(_u32(bs_version))
    p.append(_export("nif2gltf-test"))   # Author
    if bs_version < 131:
        p.append(_export(""))            # Process Script
    p.append(_export(""))                # Export Script
    if bs_version >= 103:
        p.append(_export(""))            # Max Filepath
    p.append(_u16(len(block_type_names)))
    p += [_sized(t) for t in block_type_names]
    p += [_u16(i) for i in block_type_index]
    p += [_u32(len(b)) for b in blocks]  # Block Sizes
    p.append(_u32(len(strings)))
    p.append(_u32(max((len(s) for s in strings), default=0)))
    p += [_sized(s) for s in strings]
    p.append(_u32(0))                # Num Groups
    return b"".join(p)


def build_le_nif(verts, normals, uvs, tris, node_translation=(0.0, 0.0, 0.0),
                 shape_translation=(0.0, 0.0, 0.0)):
    """NiNode(child=NiTriShape) -> NiTriShapeData."""
    types = ["NiNode", "NiTriShape", "NiTriShapeData"]
    index = [0, 1, 2]
    blocks = [
        _ninode_block([1], node_translation),
        _nitrishape_block(2, shape_translation),
        _nitrishapedata_block(verts, normals, uvs, tris),
    ]
    return _header(types, index, blocks, ["mesh"], BS_LE) + b"".join(blocks)


def _bstrishape_block(verts, normals, uvs, tris, translation=(0.0, 0.0, 0.0)):
    """SSE half-precision layout: stride 16 = vert(half3@0) uv(half2@8) normal(byte3@12)."""
    n = len(verts)
    stride = 16
    vdata = bytearray()
    for i in range(n):
        block = bytearray(stride)
        block[0:6] = _half(verts[i])
        if uvs:
            block[8:12] = _half(uvs[i])
        if normals:
            nx, ny, nz = normals[i]
            block[12] = _normbyte(nx)
            block[13] = _normbyte(ny)
            block[14] = _normbyte(nz)
        vdata += block

    attributes = 0x1 | 0x2 | 0x8           # Vertex | UVs | Normals (half precision)
    vertex_desc = (stride // 4) & 0xF       # Vertex Data Size (dwords)
    vertex_desc |= (2 & 0xF) << 8           # UV1 Offset = dword 2 (byte 8)
    vertex_desc |= (3 & 0xF) << 16          # Normal Offset = dword 3 (byte 12)
    vertex_desc |= (attributes & 0xFFF) << 44

    b = _niavobject(translation)
    b += _vec3((0, 0, 0)) + _f32(0)         # Bounding Sphere
    b += _i32(-1)                           # Skin
    b += _i32(-1)                           # Shader Property
    b += _i32(-1)                           # Alpha Property
    b += _u64(vertex_desc)
    b += _u16(len(tris))                    # Num Triangles (SSE ushort)
    b += _u16(n)                            # Num Vertices
    data_size = (stride // 4) * n * 4 + len(tris) * 6
    b += _u32(data_size)
    b += bytes(vdata)
    b += b"".join(struct.pack("<3H", *t) for t in tris)
    return b


def build_sse_nif(verts, normals, uvs, tris, node_translation=(0.0, 0.0, 0.0),
                  shape_translation=(0.0, 0.0, 0.0)):
    """NiNode(child=BSTriShape)."""
    types = ["NiNode", "BSTriShape"]
    index = [0, 1]
    blocks = [
        _ninode_block([1], node_translation),
        _bstrishape_block(verts, normals, uvs, tris, shape_translation),
    ]
    return _header(types, index, blocks, ["mesh"], BS_SSE) + b"".join(blocks)


def _bstrishape_block_fullprec(verts, normals, uvs, tris, translation=(0.0, 0.0, 0.0)):
    """SSE FULL-precision layout, with the Full_Precision attribute flag deliberately UNSET — this
    is how real vanilla statics (e.g. RockL01) decode: float3 position, but no flag advertises it.
    stride 32 = vert(float3@0) bitangentX(float@12) uv(half2@16) normal(byte3@20)."""
    n = len(verts)
    stride = 32
    vdata = bytearray()
    for i in range(n):
        block = bytearray(stride)
        struct.pack_into("<3f", block, 0, *verts[i])     # float3 position (full precision)
        if uvs:
            block[16:20] = _half(uvs[i])
        if normals:
            nx, ny, nz = normals[i]
            block[20] = _normbyte(nx); block[21] = _normbyte(ny); block[22] = _normbyte(nz)
        vdata += block

    attributes = 0x1 | 0x2 | 0x8                # Vertex | UVs | Normals — NO full-precision flag
    vertex_desc = (stride // 4) & 0xF           # Vertex Data Size = 8 dwords
    vertex_desc |= (4 & 0xF) << 8               # UV1 Offset = dword 4 (byte 16)
    vertex_desc |= (5 & 0xF) << 16              # Normal Offset = dword 5 (byte 20)
    vertex_desc |= (attributes & 0xFFF) << 44

    b = _niavobject(translation)
    b += _vec3((0, 0, 0)) + _f32(0)
    b += _i32(-1) + _i32(-1) + _i32(-1)
    b += _u64(vertex_desc)
    b += _u16(len(tris)) + _u16(n)
    b += _u32((stride // 4) * n * 4 + len(tris) * 6)
    b += bytes(vdata)
    b += b"".join(struct.pack("<3H", *t) for t in tris)
    return b


def build_sse_nif_fullprec(verts, normals, uvs, tris):
    """NiNode(child=BSTriShape) with full-precision float3 positions but no Full_Precision flag."""
    blocks = [_ninode_block([1], (0.0, 0.0, 0.0)),
              _bstrishape_block_fullprec(verts, normals, uvs, tris)]
    return _header(["NiNode", "BSTriShape"], [0, 1], blocks, ["mesh"], BS_SSE) + b"".join(blocks)

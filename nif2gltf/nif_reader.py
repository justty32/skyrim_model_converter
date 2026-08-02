"""Hand-rolled Skyrim static-mesh NIF reader -> list[Mesh] (glTF space).

Scope (MVP): NIF version 20.2.0.7, User Version 12 only (Skyrim LE bs=83 / SSE
bs>=100). Extracts triangle geometry from:
  - LE: NiTriShape / NiTriStrips -> NiTriShapeData / NiTriStripsData
  - SSE: BSTriShape / BSDynamicTriShape / BSSubIndexTriShape (BSVertexData)
Node transforms (NiNode-derived) are composed so multi-part shapes sit correctly;
geometry is converted Skyrim(Z-up) -> glTF(Y-up). Skinned/animated shapes are
rejected (SkinnedNifError -> CLI exit 3).

Robustness: Block Sizes (present since 20.2.0.5) give every block's start offset,
so after parsing each block we seek to the known next boundary — a parse drift in
one block cannot desync the rest.

Layouts taken verbatim from niftools/nifxml nif.xml (see _nifxml_ref.xml is the
downloaded reference; not committed). SSE vertex decode uses the BSVertexDesc
offset table + Full_Precision flag (the self-describing method), which is more
robust than sequential field parsing. Real vanilla-NIF byte validation is deferred
to the main machine (no game assets offline).

Structure: binary primitives live in `_binreader` (NifError/SkinnedNifError/_Reader),
per-block decoders in `_blocks`; this module owns the header parse, node-hierarchy
assembly, and the `read_nif` entry point. The public API (read_nif, NifError,
SkinnedNifError) is re-exported here unchanged.
"""

from __future__ import annotations

import numpy as np

from ._binreader import NifError, SkinnedNifError, _Reader
from ._blocks import (
    _read_bslightingshaderproperty,
    _read_bsshadertextureset,
    _read_bstrishape,
    _read_le_shape,
    _read_ninode,
    _read_nitrishapedata,
    _read_nitristripsdata,
)
from .geometry import Mesh, skyrim_to_gltf_dir, skyrim_to_gltf_point

SKYRIM_VERSION = 0x14020007
SKYRIM_USER_VERSION = 12

# NiNode-derived grouping types we know carry [transform][children] in NiNode layout.
NODE_TYPES = {
    "NiNode", "BSFadeNode", "BSLeafAnimNode", "BSOrderedNode", "BSMultiBoundNode",
    "BSValueNode", "NiBillboardNode", "NiSwitchNode", "BSTreeNode", "BSBlastNode",
    "BSDamageStage", "BSMasterParticleSystem", "BSDebrisNode", "BSRangeNode",
}
LE_SHAPE_TYPES = {"NiTriShape", "NiTriStrips"}
SSE_SHAPE_TYPES = {"BSTriShape", "BSDynamicTriShape", "BSSubIndexTriShape", "BSMeshLODTriShape", "BSLODTriShape"}
LE_DATA_TYPES = {"NiTriShapeData", "NiTriStripsData"}

__all__ = ["read_nif", "NifError", "SkinnedNifError"]


def _read_header(r: _Reader) -> dict:
    header_string = r.line()
    version = r.u32()
    if version != SKYRIM_VERSION:
        raise NifError(f"unsupported NIF version 0x{version:08X} (need Skyrim 0x14020007)")
    endian = r.u8()
    if endian != 1:
        raise NifError("big-endian NIF not supported")
    user_version = r.u32()
    if user_version != SKYRIM_USER_VERSION:
        raise NifError(f"unsupported User Version {user_version} (need 12)")
    num_blocks = r.u32()

    # BSStreamHeader (Bethesda). Always present for Skyrim user version 12.
    bs_version = r.u32()
    r.export_string()  # Author
    if bs_version > 130:
        r.u32()  # Unknown Int
    if bs_version < 131:
        r.export_string()  # Process Script
    r.export_string()  # Export Script
    if bs_version >= 103:
        r.export_string()  # Max Filepath

    num_block_types = r.u16()
    block_types = [r.sized_string() for _ in range(num_block_types)]
    block_type_index = [r.u16() for _ in range(num_blocks)]
    block_sizes = [r.u32() for _ in range(num_blocks)]  # since 20.2.0.5
    num_strings = r.u32()
    r.u32()  # Max String Length
    strings = [r.sized_string() for _ in range(num_strings)]
    num_groups = r.u32()
    for _ in range(num_groups):
        r.u32()

    types_per_block = [block_types[idx] for idx in block_type_index]
    offsets = []
    cur = r.p
    for sz in block_sizes:
        offsets.append(cur)
        cur += sz
    return {
        "header_string": header_string,
        "bs_version": bs_version,
        "num_blocks": num_blocks,
        "types": types_per_block,
        "block_sizes": block_sizes,
        "offsets": offsets,
        "strings": strings,
    }


def _world_matrix(idx: int, parent: dict, local: dict) -> np.ndarray:
    chain = []
    cur = idx
    seen = set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = parent.get(cur)
    m = np.eye(4, dtype=np.float64)
    for i in reversed(chain):
        if i in local:
            m = m @ local[i]
    return m


def _assemble_mesh(name: str, world: np.ndarray, verts, normals, uvs, tris) -> Mesh:
    rot_scale = world[:3, :3]
    out_pos = []
    for (x, y, z) in verts:
        wx, wy, wz = (world @ np.array([x, y, z, 1.0]))[:3]
        out_pos.append(skyrim_to_gltf_point(float(wx), float(wy), float(wz)))
    out_nrm = []
    for (x, y, z) in normals:
        nx, ny, nz = rot_scale @ np.array([x, y, z], dtype=np.float64)
        n = np.array([nx, ny, nz])
        ln = np.linalg.norm(n)
        if ln > 1e-9:
            n = n / ln
        out_nrm.append(skyrim_to_gltf_dir(float(n[0]), float(n[1]), float(n[2])))
    return Mesh(name=name, positions=out_pos, normals=out_nrm, uvs=list(uvs), triangles=list(tris))


def _resolve_diffuse(blocks: dict, shader_ref) -> str:
    """shape.shader_ref -> BSLightingShaderProperty.texset_ref -> BSShaderTextureSet.diffuse."""
    sh = blocks.get(shader_ref)
    if not sh or sh.get("kind") != "shader":
        return ""
    ts = blocks.get(sh.get("texset_ref"))
    if not ts or ts.get("kind") != "texset":
        return ""
    return ts.get("diffuse", "")


def read_nif(data: bytes) -> list[Mesh]:
    """Parse NIF bytes -> list[Mesh] in glTF space. Raises NifError / SkinnedNifError."""
    r = _Reader(data)
    hdr = _read_header(r)
    types = hdr["types"]
    offsets = hdr["offsets"]
    sizes = hdr["block_sizes"]
    bs_version = hdr["bs_version"]

    blocks: dict[int, dict] = {}
    for i in range(hdr["num_blocks"]):
        r.seek(offsets[i])
        t = types[i]
        try:
            if t in NODE_TYPES:
                blocks[i] = _read_ninode(r)
            elif t in LE_SHAPE_TYPES:
                blocks[i] = _read_le_shape(r)
                # In SSE-format nifs (bs>=100) a NiTriShape ends with its BSShaderProperty + NiAlpha
                # refs; the shader ref is the second-to-last i32 (block_end - 8). True LE nifs (bs 83)
                # use a property list instead, so only harvest it for SSE.
                if bs_version >= 100 and sizes[i] >= 8:
                    r.seek(offsets[i] + sizes[i] - 8)
                    blocks[i]["shader_ref"] = r.i32()
            elif t == "NiTriShapeData":
                blocks[i] = _read_nitrishapedata(r)
            elif t == "NiTriStripsData":
                blocks[i] = _read_nitristripsdata(r)
            elif t in SSE_SHAPE_TYPES:
                blocks[i] = _read_bstrishape(r, bs_version)
            elif t == "BSLightingShaderProperty":
                blocks[i] = _read_bslightingshaderproperty(r, offsets[i])
            elif t == "BSShaderTextureSet":
                blocks[i] = _read_bsshadertextureset(r)
        except NifError:
            # Defensive: a malformed sub-block is skipped, not fatal (boundary seek recovers).
            blocks.pop(i, None)

    # Build node hierarchy (child -> parent) and per-block local matrices.
    parent: dict[int, int] = {}
    local: dict[int, np.ndarray] = {}
    for i, b in blocks.items():
        if "local" in b:
            local[i] = b["local"]
        if b.get("kind") == "node":
            for c in b["children"]:
                if c >= 0:
                    parent[c] = i

    meshes: list[Mesh] = []
    skinned_seen = False
    for i, b in blocks.items():
        kind = b.get("kind")
        if kind == "le_shape":
            if b["skinned"]:
                skinned_seen = True
                continue
            data = blocks.get(b["data_ref"])
            if not data or data.get("kind") != "data" or not data["verts"]:
                continue
            world = _world_matrix(i, parent, local)
            mesh = _assemble_mesh(
                f"shape_{i}", world, data["verts"], data["normals"], data["uvs"], data["tris"])
            mesh.texture = _resolve_diffuse(blocks, b.get("shader_ref"))
            meshes.append(mesh)
        elif kind == "sse_shape":
            if b["skinned"]:
                skinned_seen = True
                continue
            if not b["verts"]:
                continue
            world = _world_matrix(i, parent, local)
            mesh = _assemble_mesh(
                f"shape_{i}", world, b["verts"], b["normals"], b["uvs"], b["tris"])
            mesh.texture = _resolve_diffuse(blocks, b.get("shader_ref"))
            meshes.append(mesh)

    if not meshes:
        if skinned_seen:
            raise SkinnedNifError("NIF contains only skinned/animated geometry (static backend)")
        raise NifError("no static triangle geometry found")
    return meshes

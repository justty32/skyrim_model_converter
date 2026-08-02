"""Assemble a Skyrim SSE static NIF (20.2.0.7 / user 12 / BSVersion 100) from Mesh IR.

Every byte layout here was verified against real vanilla SSE nifs (a shipped mesh
with BSTriShape + BSLightingShaderProperty + bhkRigidBody) and against nif2gltf's
reader — whatever nif2gltf reads back, this writes. See README.md for the field
tables and where each constant came from.

Block plan:
    0                : NiNode (root; children = every shape; collision ref if hulls)
    per shape (i)    : BSTriShape, BSLightingShaderProperty, BSShaderTextureSet
    collision (opt)  : bhkCollisionObject, bhkRigidBody, bhkListShape,
                       bhkConvexVerticesShape * N
"""

from __future__ import annotations

import numpy as np

from ._binwriter import _Writer
from .collision import Hull
from .geometry import (
    Mesh,
    compute_tangents,
    face_normals,
    gltf_to_skyrim_dir,
    gltf_to_skyrim_point,
)

NIF_VERSION = 0x14020007
USER_VERSION = 12
BS_VERSION = 100  # Skyrim Special Edition

# --- BSVertexDesc (matches vanilla static: stride 28, full-precision float3 position,
#     but WITHOUT the VF_FULLPREC(0x400) attribute bit — vanilla omits it and nif2gltf
#     infers precision from the UV offset >= 12, so we omit it too for a byte match). ---
_VF_VERTEX, _VF_UV, _VF_NORMALS, _VF_TANGENTS = 0x1, 0x2, 0x8, 0x10
_STRIDE = 28
_UV_OFFSET, _NRM_OFFSET, _TAN_OFFSET = 16, 20, 24
_ATTRS = _VF_VERTEX | _VF_UV | _VF_NORMALS | _VF_TANGENTS  # 0x1B


def _vertex_desc() -> int:
    return (
        (_STRIDE // 4) & 0xF
        | ((_UV_OFFSET // 4) & 0xF) << 8
        | ((_NRM_OFFSET // 4) & 0xF) << 16
        | ((_TAN_OFFSET // 4) & 0xF) << 20
        | (_ATTRS & 0xFFF) << 44
    )


def _nbyte(c: float) -> int:
    """Encode a [-1,1] normal component as a byte (inverse of nif2gltf byte/255*2-1)."""
    return max(0, min(255, round((c + 1.0) / 2.0 * 255.0)))


# --- BSLightingShaderProperty defaults (opaque static, Default shader type -> 100-byte
#     block). Values taken from a real shipped SSE opaque+normal-mapped static mesh; the
#     two "reserved" NiObjectNET words at +4/+8 are copied verbatim from vanilla (a -1 ref
#     and a 0). See README for the full offset table. ---
# SLSF1: vanilla static combo (Specular | Recv_Shadows | Cast_Shadows | engine-default
# high bits), verified vs Skyrim.esm SFarmhouseSilo. The earlier 0x82408009 (copied from
# a mod mesh) carried Vertex_Alpha on a mesh with NO vertex colors — bad combo.
_LSP_SHADER_FLAGS1 = 0x82400301
# SLSF2: vanilla static base 0x8021 + Double_Sided (0x10). DS map pieces are authored
# to be viewed from INSIDE (wall front faces point into the corridor); single-sided
# rendering makes flat walls invisible from outside while thin trims still show.
# Double-sided sidesteps the whole orientation question for ported geometry.
_LSP_SHADER_FLAGS2 = 0x00008021 | 0x00000010
_LSP_GLOSSINESS = 80.0
_LSP_SPEC_STRENGTH = 1.0
_LSP_EMISSIVE_MULT = 1.0
_LSP_LIGHTING_EFFECT_1 = 0.3
_LSP_LIGHTING_EFFECT_2 = 2.0
_TEX_CLAMP_WRAP = 3  # WRAP_S_WRAP_T

# --- Havok constants (static, immovable). Enum values from nif.xml. ---
_HAVOK_MAT_STONE = 3741512247   # SKY_HAV_MAT_STONE
_LAYER_STATIC = 1               # SKYL_STATIC
_RESPONSE_SIMPLE = 1            # RESPONSE_SIMPLE_CONTACT
_BROAD_PHASE_ENTITY = 1
_MOTION_FIXED = 7               # MO_SYS_FIXED
_DEACTIVATOR_NEVER = 1
_SOLVER_DEACT_OFF = 1
_QUALITY_INVALID = 0            # MO_QUAL_INVALID (vanilla static)
_CINFO_PROPERTY = (0, 0, 0x80000000)  # bhkWorldObjCInfoProperty default
_CONVEX_RADIUS = 0.05           # bhkConvexShape shell radius default


def _bounding_sphere(sk_positions):
    p = np.asarray(sk_positions, dtype=np.float64)
    lo, hi = p.min(axis=0), p.max(axis=0)
    center = (lo + hi) * 0.5
    radius = float(np.linalg.norm(p - center, axis=1).max()) if len(p) else 0.0
    return center, radius


def _cinfo_property(w: _Writer) -> None:
    w.u32(_CINFO_PROPERTY[0])
    w.u32(_CINFO_PROPERTY[1])
    w.u32(_CINFO_PROPERTY[2])


def _havok_filter(w: _Writer, layer: int) -> None:
    w.u8(layer)   # Layer
    w.u8(0)       # Flags
    w.u16(0)      # Group


# ---------------------------------------------------------------- block builders

def _build_bsxflags(name_idx: int, value: int) -> bytes:
    # NiIntegerExtraData: vanilla statics hang a BSXFlags off the root
    # (0x2 = Havok/collision present).
    w = _Writer()
    w.u32(name_idx)          # Name ("BSX")
    w.u32(value)             # Integer Data
    return bytes(w.buf)


def _build_ninode(name_idx: int, child_refs, collision_ref: int,
                  extra_refs: list[int] | None = None) -> bytes:
    w = _Writer()
    w.u32(name_idx)          # Name
    w.u32(len(extra_refs or []))  # Num Extra Data List
    for e in (extra_refs or []):
        w.i32(e)
    w.i32(-1)                # Controller
    w.u32(0x0008000E)        # Flags (vanilla statics: 0x8000E, not 0xE)
    w.vec3((0.0, 0.0, 0.0))  # Translation
    w.mat33(np.eye(3))       # Rotation
    w.f32(1.0)               # Scale
    w.i32(collision_ref)     # Collision Object
    w.u32(len(child_refs))
    for c in child_refs:
        w.i32(c)
    w.u32(0)                 # Num Effects
    return bytes(w.buf)


def _build_bstrishape(name_idx: int, shader_ref: int, mesh: Mesh) -> bytes:
    # Transform geometry glTF(Y-up m) -> Skyrim(Z-up units); axis swap for normals.
    sk_pos = [gltf_to_skyrim_point(*p) for p in mesh.positions]
    if mesh.has_normals:
        gl_nrm = mesh.normals
    else:
        gl_nrm = face_normals(mesh.positions, mesh.triangles)
    sk_nrm = []
    for n_ in gl_nrm:
        nx, ny, nz = gltf_to_skyrim_dir(*n_)
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        sk_nrm.append((nx / ln, ny / ln, nz / ln))
    uvs = mesh.uvs if mesh.has_uvs else [(0.0, 0.0)] * len(sk_pos)
    tangents, bitangents = compute_tangents(sk_pos, sk_nrm, uvs, mesh.triangles)

    n = len(sk_pos)
    center, radius = _bounding_sphere(sk_pos)

    w = _Writer()
    w.u32(name_idx)          # Name
    w.u32(0)                 # Num Extra Data List
    w.i32(-1)                # Controller
    w.u32(0x0008000E)        # Flags (match vanilla static shapes)
    w.vec3((0.0, 0.0, 0.0))  # Translation
    w.mat33(np.eye(3))       # Rotation
    w.f32(1.0)               # Scale
    w.i32(-1)                # Collision Object
    w.vec3(center)           # Bounding Sphere center
    w.f32(radius)            # Bounding Sphere radius
    w.i32(-1)                # Skin
    w.i32(shader_ref)        # Shader Property
    w.i32(-1)                # Alpha Property
    w.u64(_vertex_desc())    # Vertex Desc
    w.u16(len(mesh.triangles))  # Num Triangles (SSE ushort)
    w.u16(n)                 # Num Vertices
    w.u32(_STRIDE * n + len(mesh.triangles) * 6)  # Data Size

    for i in range(n):
        vx, vy, vz = sk_pos[i]
        w.f32(vx); w.f32(vy); w.f32(vz)          # @0  Vertex float3
        w.f32(bitangents[i][0])                  # @12 Bitangent X
        w.half2(uvs[i])                          # @16 UV half2
        nx, ny, nz = sk_nrm[i]
        w.u8(_nbyte(nx)); w.u8(_nbyte(ny)); w.u8(_nbyte(nz))  # @20 Normal byte3
        w.u8(_nbyte(bitangents[i][1]))           # @23 Bitangent Y
        tx, ty, tz = tangents[i]
        w.u8(_nbyte(tx)); w.u8(_nbyte(ty)); w.u8(_nbyte(tz))  # @24 Tangent byte3
        w.u8(_nbyte(bitangents[i][2]))           # @27 Bitangent Z

    for a, b, c in mesh.triangles:
        w.u16(a); w.u16(b); w.u16(c)
    # Trailing u32(0): present in every vanilla SSE BSTriShape (byte-verified vs
    # Skyrim.esm SFarmhouseSilo — 4 zero bytes after the triangle list, included in
    # the block size). The engine reads blocks SEQUENTIALLY, so omitting it shifts
    # every later field by 4 and a length field becomes garbage -> giant memcpy CTD.
    w.u32(0)
    return bytes(w.buf)


def _build_lsp(name_idx: int, texset_ref: int) -> bytes:
    w = _Writer()
    w.u32(name_idx)              # +0  Name
    w.u32(0xFFFFFFFF)            # +4  reserved (vanilla -1)
    w.u32(0x00000000)           # +8  reserved (vanilla 0)
    w.i32(-1)                    # +12 Controller
    w.u32(_LSP_SHADER_FLAGS1)    # +16 Shader Flags 1
    w.u32(_LSP_SHADER_FLAGS2)    # +20 Shader Flags 2
    w.f32(0.0); w.f32(0.0)       # +24 UV Offset
    w.f32(1.0); w.f32(1.0)       # +32 UV Scale
    w.i32(texset_ref)            # +40 Texture Set
    w.f32(0.0); w.f32(0.0); w.f32(0.0)   # +44 Emissive Color
    w.f32(_LSP_EMISSIVE_MULT)    # +56 Emissive Multiple
    w.u32(_TEX_CLAMP_WRAP)       # +60 Texture Clamp Mode
    w.f32(1.0)                   # +64 Alpha
    w.f32(0.0)                   # +68 Refraction Strength
    w.f32(_LSP_GLOSSINESS)       # +72 Glossiness
    w.f32(1.0); w.f32(1.0); w.f32(1.0)   # +76 Specular Color
    w.f32(_LSP_SPEC_STRENGTH)    # +88 Specular Strength
    w.f32(_LSP_LIGHTING_EFFECT_1)  # +92 Lighting Effect 1
    w.f32(_LSP_LIGHTING_EFFECT_2)  # +96 Lighting Effect 2
    return bytes(w.buf)          # 100 bytes


def _build_texset(paths: list[str]) -> bytes:
    w = _Writer()
    slots = (paths + [""] * 9)[:9]  # vanilla SSE = 9 texture slots
    w.u32(len(slots))
    for s in slots:
        w.sized_string(s)
    return bytes(w.buf)


def _build_collision_object(target_ref: int, body_ref: int) -> bytes:
    w = _Writer()
    w.u32(target_ref)  # Target (Ptr to root NiNode)
    w.u16(0x0081)      # Flags (SYNC_ON_UPDATE, vanilla bhkCollisionObject default)
    w.u32(body_ref)    # Body
    return bytes(w.buf)


def _build_rigidbody(shape_ref: int) -> bytes:
    w = _Writer()
    # bhkWorldObject
    w.i32(shape_ref)                 # Shape
    _havok_filter(w, _LAYER_STATIC)  # Havok Filter
    w.raw(b"\x00\x00\x00\x00")       # World Object Info: Unused01
    w.u8(_BROAD_PHASE_ENTITY)        # Broad Phase Type
    w.raw(b"\x00\x00\x00")           # Unused02
    _cinfo_property(w)               # Property
    # bhkEntity
    w.u8(_RESPONSE_SIMPLE); w.u8(0); w.u16(0xFFFF)  # EntityCInfo
    # bhkRigidBodyCInfo2010
    w.raw(b"\x00" * 4)               # Unused01
    _havok_filter(w, _LAYER_STATIC)  # Havok Filter
    w.raw(b"\x00" * 4)               # Unused02
    w.u32(0)                         # Unknown Int 1
    w.u8(_RESPONSE_SIMPLE); w.u8(0); w.u16(0xFFFF)  # Response/Unused/Delay
    w.vec4((0.0, 0.0, 0.0, 0.0))     # Translation
    w.vec4((0.0, 0.0, 0.0, 1.0))     # Rotation (quaternion x,y,z,w)
    w.vec4((0.0, 0.0, 0.0, 0.0))     # Linear Velocity
    w.vec4((0.0, 0.0, 0.0, 0.0))     # Angular Velocity
    w.raw(b"\x00" * 48)              # Inertia Tensor (hkMatrix3; 0 for immovable)
    w.vec4((0.0, 0.0, 0.0, 0.0))     # Center
    w.f32(0.0)                       # Mass (0 = immovable static)
    w.f32(0.1)                       # Linear Damping
    w.f32(0.05)                      # Angular Damping
    w.f32(1.0)                       # Time Factor
    w.f32(1.0)                       # Gravity Factor
    w.f32(0.5)                       # Friction
    w.f32(0.0)                       # Rolling Friction Multiplier
    w.f32(0.4)                       # Restitution
    w.f32(104.4)                     # Max Linear Velocity
    w.f32(31.57)                     # Max Angular Velocity
    w.f32(0.15)                      # Penetration Depth
    w.u8(_MOTION_FIXED)              # Motion System
    w.u8(_DEACTIVATOR_NEVER)         # Deactivator Type
    w.u8(_SOLVER_DEACT_OFF)          # Solver Deactivation
    w.u8(_QUALITY_INVALID)           # Quality Type
    w.u8(0)                          # Auto Remove Level
    w.u8(0)                          # Response Modifier Flags
    w.u8(3)                          # Num Shape Keys in Contact Point
    w.u8(0)                          # Force Collided Onto PPU
    # Unused04: vanilla (SFarmhouseSilo AND Basket01) both carry -1 in the first
    # dword of this region; keep byte parity with the engine's own files.
    w.raw(b"\xff\xff\xff\xff" + b"\x00" * 8)
    w.u32(0)                         # Num Constraints
    w.u16(0)                         # Body Flags (BSVER >= 76)
    return bytes(w.buf)


def _build_listshape(sub_refs: list[int]) -> bytes:
    w = _Writer()
    w.u32(len(sub_refs))          # Num Sub Shapes
    for r in sub_refs:
        w.i32(r)                  # Sub Shapes
    w.u32(_HAVOK_MAT_STONE)       # Material
    _cinfo_property(w)            # Child Shape Property
    _cinfo_property(w)            # Child Filter Property
    w.u32(len(sub_refs))          # Num Filters
    for _ in sub_refs:
        w.u32(0)                  # Filters (HavokFilter, zeroed)
    return bytes(w.buf)


def _build_convex_vertices(hull: Hull) -> bytes:
    w = _Writer()
    w.u32(_HAVOK_MAT_STONE)       # Material
    w.f32(_CONVEX_RADIUS)         # Radius
    _cinfo_property(w)            # Vertices Property
    _cinfo_property(w)            # Normals Property
    w.u32(len(hull.vertices))     # Num Vertices
    for v in hull.vertices:
        w.vec4((v[0], v[1], v[2], 0.0))
    w.u32(len(hull.planes))       # Num Normals
    for nrm, d in hull.planes:
        w.vec4((nrm[0], nrm[1], nrm[2], d))
    return bytes(w.buf)


# ---------------------------------------------------------------- header + top level

def _slot_paths(mesh: Mesh, texprefix: str, has_normal: bool) -> list[str]:
    """Diffuse (slot0) and, if present, normal (slot1) .dds paths for a material."""
    base = mesh.material
    if not base:
        return []
    prefix = texprefix.rstrip("\\/") + "\\" if texprefix else ""
    diffuse = f"{prefix}{base}.dds"
    normal = f"{prefix}{base}_n.dds" if has_normal else ""
    return [diffuse, normal]


def build_nif(meshes: list[Mesh], texprefix: str, normal_map_flags: list[bool],
              hulls: list[Hull] | None = None, root_name: str = "Scene Root") -> bytes:
    """Serialise Mesh IR (+ optional collision hulls) to Skyrim SSE NIF bytes."""
    for m in meshes:
        if len(m.positions) > 0xFFFF:
            raise ValueError(f"shape '{m.name}' has {len(m.positions)} verts > 65535 "
                             "(SSE BSTriShape is 16-bit; split the mesh)")

    strings: list[str] = [root_name, "BSX"]
    blocks: list[tuple[str, bytes]] = []

    # Reserve index 0 for the root; fill it in last (needs child + collision refs).
    # Vanilla statics root on a BSFadeNode with a BSXFlags extra (block 1) — mirror that.
    blocks.append(("BSFadeNode", b""))
    bsx_ref = len(blocks)
    blocks.append(("BSXFlags", _build_bsxflags(1, 0x2 if hulls else 0x0)))
    shape_refs: list[int] = []

    # Collision chain BEFORE the meshes, children before parents (convex -> list ->
    # rigid body -> collision object). Every vanilla sample (SFarmhouseSilo, Basket01,
    # Bucket01) orders bhk blocks bottom-up; our old top-down order made every bhk ref
    # a FORWARD reference and the engine's sequential loader linked a not-yet-built
    # child -> null hkpShape -> CTD while streaming the model in.
    collision_ref = -1
    if hulls:
        convex_start = len(blocks)
        for h in hulls:
            blocks.append(("bhkConvexVerticesShape", _build_convex_vertices(h)))
        if len(hulls) > 1:
            shape_for_rb = len(blocks)
            blocks.append(("bhkListShape", _build_listshape(
                list(range(convex_start, convex_start + len(hulls))))))
        else:
            # Single hull: hang the convex shape straight off the rigid body,
            # exactly like vanilla Basket01 (no bhkListShape indirection).
            shape_for_rb = convex_start
        rb_idx = len(blocks)
        blocks.append(("bhkRigidBody", _build_rigidbody(shape_for_rb)))
        collision_ref = len(blocks)
        blocks.append(("bhkCollisionObject", _build_collision_object(0, rb_idx)))

    for m in meshes:
        shape_idx = len(blocks)
        lsp_idx = shape_idx + 1
        texset_idx = shape_idx + 2
        name_idx = len(strings)
        strings.append(m.name or f"shape_{shape_idx}")
        has_n = normal_map_flags[len(shape_refs)] if normal_map_flags else False
        blocks.append(("BSTriShape", _build_bstrishape(name_idx, lsp_idx, m)))
        blocks.append(("BSLightingShaderProperty", _build_lsp(0, texset_idx)))
        blocks.append(("BSShaderTextureSet",
                       _build_texset(_slot_paths(m, texprefix, has_n))))
        shape_refs.append(shape_idx)

    blocks[0] = ("BSFadeNode", _build_ninode(0, shape_refs, collision_ref,
                                             extra_refs=[bsx_ref]))

    return _assemble(blocks, strings)


def _assemble(blocks: list[tuple[str, bytes]], strings: list[str]) -> bytes:
    type_names: list[str] = []
    type_index: list[int] = []
    for t, _ in blocks:
        if t not in type_names:
            type_names.append(t)
        type_index.append(type_names.index(t))

    h = _Writer()
    h.line("Gamebryo File Format, Version 20.2.0.7")
    h.u32(NIF_VERSION)
    h.u8(1)                  # little-endian
    h.u32(USER_VERSION)
    h.u32(len(blocks))
    h.u32(BS_VERSION)
    h.export_string("gltf2nif")   # Author
    if BS_VERSION > 130:
        h.u32(0)                  # Unknown Int
    if BS_VERSION < 131:
        h.export_string("")       # Process Script
    h.export_string("")           # Export Script
    if BS_VERSION >= 103:
        h.export_string("")       # Max Filepath
    h.u16(len(type_names))
    for t in type_names:
        h.sized_string(t)
    for idx in type_index:
        h.u16(idx)
    for _, b in blocks:
        h.u32(len(b))             # Block Sizes
    h.u32(len(strings))
    h.u32(max((len(s) for s in strings), default=0))
    for s in strings:
        h.sized_string(s)
    h.u32(0)                      # Num Groups

    out = bytearray(h.buf)
    for _, b in blocks:
        out += b
    # NiFooter: the engine reads Num Roots + root refs after the last block.
    # Omitting it makes the runtime parse past the end of the block data
    # (garbage root count -> heap corruption in-game), even though offline
    # readers that stop at the last block never notice.
    footer = _Writer()
    footer.u32(1)                 # Num Roots
    footer.i32(0)                 # -> root NiNode (block 0)
    out += footer.buf
    return bytes(out)

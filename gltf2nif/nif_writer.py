"""Assemble a Skyrim SSE static NIF (20.2.0.7 / user 12 / BSVersion 100) from Mesh IR.

Every byte layout here was verified against real vanilla SSE nifs (a shipped mesh
with BSTriShape + BSLightingShaderProperty + bhkRigidBody) and against nif2gltf's
reader — whatever nif2gltf reads back, this writes. See README.md for the field
tables and where each constant came from.

Block plan:
    0                : NiNode (root; children = every shape; collision ref if hulls)
    per shape (i)    : [NiAlphaProperty], BSTriShape, BSLightingShaderProperty,
                       BSShaderTextureSet
                       (NiAlphaProperty only when a MaterialSpec asks for MASK/BLEND)
    collision (opt)  : bhkCollisionObject, bhkRigidBody, bhkListShape,
                       bhkConvexVerticesShape * N
"""

from __future__ import annotations

import numpy as np

from ._binwriter import _Writer
from .collision import Hull
from .material import (
    MaterialSpec,
    metallic_specular_color,
    metallic_to_specular_strength,
    roughness_to_glossiness,
)
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
_VF_COLORS = 0x200
_STRIDE = 28
_UV_OFFSET, _NRM_OFFSET, _TAN_OFFSET = 16, 20, 24
_ATTRS = _VF_VERTEX | _VF_UV | _VF_NORMALS | _VF_TANGENTS  # 0x1B
_COLOR_OFFSET = 28
_COLOR_STRIDE = 32


def _vertex_desc(has_colors: bool = False) -> int:
    stride = _COLOR_STRIDE if has_colors else _STRIDE
    attrs = _ATTRS | (_VF_COLORS if has_colors else 0)
    return (
        (stride // 4) & 0xF
        | ((_UV_OFFSET // 4) & 0xF) << 8
        | ((_NRM_OFFSET // 4) & 0xF) << 16
        | ((_TAN_OFFSET // 4) & 0xF) << 20
        | (((_COLOR_OFFSET // 4) & 0xF) << 24 if has_colors else 0)
        | (attrs & 0xFFF) << 44
    )


def _nbyte(c: float) -> int:
    """Encode a [-1,1] normal component as a byte (inverse of nif2gltf byte/255*2-1)."""
    return max(0, min(255, round((c + 1.0) / 2.0 * 255.0)))


def _ubyte01(c: float) -> int:
    """Encode a clamped [0,1] colour component as an unsigned byte."""
    return max(0, min(255, round(float(c) * 255.0)))


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
_LSP_SHADER_FLAGS2_BASE = 0x00008021
_SLSF1_SPECULAR = 0x00000001
_SLSF2_DOUBLE_SIDED = 0x00000010
_LSP_SHADER_FLAGS2 = _LSP_SHADER_FLAGS2_BASE | _SLSF2_DOUBLE_SIDED
_LSP_GLOSSINESS = 80.0
_LSP_SPEC_STRENGTH = 1.0
_LSP_EMISSIVE_MULT = 1.0
_LSP_LIGHTING_EFFECT_1 = 0.3
_LSP_LIGHTING_EFFECT_2 = 2.0
_TEX_CLAMP_WRAP = 3  # WRAP_S_WRAP_T

# --- NiAlphaProperty flag words (nif.xml AlphaFlags bitfield) ---
#   bit0      alpha blend enable
#   bits1-4   source blend mode      (6 = SRC_ALPHA)
#   bits5-8   destination blend mode (7 = INV_SRC_ALPHA)
#   bit9      alpha test enable
#   bits10-12 alpha test function    (4 = GREATER)
# BLEND: 0x00ED = blend on | src SRC_ALPHA (6<<1=0x0C) | dst INV_SRC_ALPHA (7<<5=0xE0)
#        -> 0x01 | 0x0C | 0xE0 = 0xED. This is the value every vanilla alpha-blended
#        SSE mesh (glass, foliage billboards) carries.
# MASK : 0x0201 = alpha test on (0x200) | test function GREATER (0<<10) ... plus bit0.
#        Vanilla alpha-TEST meshes (tree leaves) ship 0x0201: the low bit is set even
#        though the blend is a no-op, matching the engine's own files, and the cutoff
#        lives in Threshold. Keeping byte parity with vanilla beats deriving it.
_ALPHA_FLAGS_BLEND = 0x00ED
_ALPHA_FLAGS_MASK = 0x0201

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


def _build_bstrishape(name_idx: int, shader_ref: int, mesh: Mesh,
                      alpha_ref: int = -1) -> bytes:
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
    w.i32(alpha_ref)         # Alpha Property (-1 = none, the historical default)
    has_colors = mesh.has_colors
    stride = _COLOR_STRIDE if has_colors else _STRIDE
    w.u64(_vertex_desc(has_colors))  # Vertex Desc
    w.u16(len(mesh.triangles))  # Num Triangles (SSE ushort)
    w.u16(n)                 # Num Vertices
    w.u32(stride * n + len(mesh.triangles) * 6)  # Data Size

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
        if has_colors:
            r, g, b, a = mesh.colors[i]
            w.u8(_ubyte01(r)); w.u8(_ubyte01(g)); w.u8(_ubyte01(b)); w.u8(_ubyte01(a))

    for a, b, c in mesh.triangles:
        w.u16(a); w.u16(b); w.u16(c)
    # Trailing u32(0): present in every vanilla SSE BSTriShape (byte-verified vs
    # Skyrim.esm SFarmhouseSilo — 4 zero bytes after the triangle list, included in
    # the block size). The engine reads blocks SEQUENTIALLY, so omitting it shifts
    # every later field by 4 and a length field becomes garbage -> giant memcpy CTD.
    w.u32(0)
    return bytes(w.buf)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _build_lsp(name_idx: int, texset_ref: int, spec: MaterialSpec | None = None,
               *, has_vertex_colors: bool = False) -> bytes:
    """BSLightingShaderProperty (100 bytes, Default shader type).

    With `spec is None` every field is the historical constant, so the bytes are
    identical to what this writer has always produced (the backward-compat gate).

    With a MaterialSpec, glTF metallic/roughness PBR is folded into Skyrim's
    Blinn-Phong-era parameters. Those mappings are PERCEPTUAL APPROXIMATIONS, NOT
    physical equivalences -- see gltf2nif/material.py for each formula and why:
        Glossiness       = clamp(2 ** ((1 - roughness) * 9 + 1), 2, 999)
        Specular Strength= clamp((0.2 + 0.8 * metallic) * KHR specularFactor, 0, 1)
        Specular Color   = lerp(white, base_color.rgb, metallic)   # F0 stand-in
        Alpha            = base_color.a
        Emissive Color   = emissiveFactor, Emissive Multiple = KHR emissiveStrength
    """
    flags1 = _LSP_SHADER_FLAGS1
    flags2 = _LSP_SHADER_FLAGS2
    emissive = (0.0, 0.0, 0.0)
    emissive_mult = _LSP_EMISSIVE_MULT
    alpha = 1.0
    glossiness = _LSP_GLOSSINESS
    specular_color = (1.0, 1.0, 1.0)
    specular_strength = _LSP_SPEC_STRENGTH

    if spec is not None:
        emissive = (float(spec.emissive[0]), float(spec.emissive[1]), float(spec.emissive[2]))
        emissive_mult = float(spec.emissive_strength)
        alpha = _clamp01(float(spec.base_color[3]))
        glossiness = roughness_to_glossiness(spec.roughness)
        specular_strength = metallic_to_specular_strength(spec.metallic, spec.specular_factor)
        specular_color = metallic_specular_color(spec.base_color, spec.metallic)
        # Double_Sided is force-ON in the spec-less path on purpose (DarkSouls map
        # pieces are authored to be seen from inside). Once a real material speaks
        # for itself, honour it instead.
        flags2 = _LSP_SHADER_FLAGS2_BASE | (_SLSF2_DOUBLE_SIDED if spec.double_sided else 0)
        # A fully-rough non-metal has no meaningful highlight; dropping SLSF1 Specular
        # saves the specular pass. Anything else keeps the vanilla-static combo.
        if float(spec.metallic) == 0.0 and float(spec.roughness) >= 0.95:
            flags1 &= ~_SLSF1_SPECULAR
    if has_vertex_colors:
        flags2 |= 0x00000080  # SLSF2_Vertex_Colors

    w = _Writer()
    w.u32(name_idx)              # +0  Name
    w.u32(0xFFFFFFFF)            # +4  reserved (vanilla -1)
    w.u32(0x00000000)           # +8  reserved (vanilla 0)
    w.i32(-1)                    # +12 Controller
    w.u32(flags1)                # +16 Shader Flags 1
    w.u32(flags2)                # +20 Shader Flags 2
    w.f32(0.0); w.f32(0.0)       # +24 UV Offset
    w.f32(1.0); w.f32(1.0)       # +32 UV Scale
    w.i32(texset_ref)            # +40 Texture Set
    w.f32(emissive[0]); w.f32(emissive[1]); w.f32(emissive[2])  # +44 Emissive Color
    w.f32(emissive_mult)         # +56 Emissive Multiple
    w.u32(_TEX_CLAMP_WRAP)       # +60 Texture Clamp Mode
    w.f32(alpha)                 # +64 Alpha
    w.f32(0.0)                   # +68 Refraction Strength
    w.f32(glossiness)            # +72 Glossiness
    w.f32(specular_color[0]); w.f32(specular_color[1]); w.f32(specular_color[2])  # +76
    w.f32(specular_strength)     # +88 Specular Strength
    w.f32(_LSP_LIGHTING_EFFECT_1)  # +92 Lighting Effect 1
    w.f32(_LSP_LIGHTING_EFFECT_2)  # +96 Lighting Effect 2
    return bytes(w.buf)          # 100 bytes


def _alpha_settings(spec: MaterialSpec | None) -> tuple[int, int] | None:
    """(NiAlphaProperty Flags, Threshold) for a spec, or None when no block is needed."""
    if spec is None:
        return None
    mode = (spec.alpha_mode or "OPAQUE").upper()
    if mode == "BLEND":
        flags = (_ALPHA_FLAGS_BLEND if spec.alpha_flags_override is None
                 else int(spec.alpha_flags_override))
        threshold = (0 if spec.alpha_threshold_override is None
                     else int(spec.alpha_threshold_override))
        return flags, threshold
    if mode == "MASK":
        flags = (_ALPHA_FLAGS_MASK if spec.alpha_flags_override is None
                 else int(spec.alpha_flags_override))
        threshold = (int(round(_clamp01(float(spec.alpha_cutoff)) * 255.0))
                     if spec.alpha_threshold_override is None
                     else int(spec.alpha_threshold_override))
        return flags, threshold
    return None


def _build_alpha_property(name_idx: int, flags: int, threshold: int) -> bytes:
    """NiAlphaProperty = NiObjectNET header + Flags(u16) + Threshold(u8) = 15 bytes.

    Deliberately conservative on the shader side: we set NO extra SLSF1 bit. In
    SkyrimShaderPropertyFlags1, 0x1000 is Model_Space_Normals (NOT an alpha flag) and
    0x8 is Vertex_Alpha (which would read a vertex-colour channel this writer never
    emits) -- setting either would be a visual bug. Alpha behaviour in SSE is driven
    by the NiAlphaProperty block itself, so the ref + this block are enough.
    """
    w = _Writer()
    w.u32(name_idx)          # Name
    w.u32(0)                 # Num Extra Data List
    w.i32(-1)                # Controller
    w.u16(flags)             # Flags
    w.u8(threshold)          # Threshold
    return bytes(w.buf)


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

def _slot_paths(mesh: Mesh, texprefix: str, has_normal: bool,
                spec: MaterialSpec | None = None) -> list[str]:
    """BSShaderTextureSet slot paths for a material.

    Always slot0 diffuse and (when `has_normal`) slot1 normal -- unchanged.
    With a MaterialSpec we may additionally fill the SSE slots:
        slot2 = <base>_g.dds  glow/emissive map  (only if the material has one)
        slot7 = <base>_s.dds  specular/backlight (only if the material has one)
    Empty slots stay empty strings, so a spec that declares neither map produces
    exactly the historical two-slot set.
    """
    base = mesh.material or (spec.base_name if spec is not None else "")
    if not base:
        return []
    prefix = texprefix.rstrip("\\/") + "\\" if texprefix else ""
    diffuse = f"{prefix}{base}.dds"
    normal = f"{prefix}{base}_n.dds" if has_normal else ""
    if spec is None:
        return [diffuse, normal]
    if spec.normal_texture_name:
        normal = spec.normal_texture_name
    slots = [diffuse, normal] + [""] * 6  # slots 0..7 (slot8 is padded by _build_texset)
    if spec.has_emissive_map:
        slots[2] = f"{prefix}{base}_g.dds"
    if spec.has_specular_map:
        slots[7] = f"{prefix}{base}_s.dds"
    return slots


def build_nif(meshes: list[Mesh], texprefix: str, normal_map_flags: list[bool],
              hulls: list[Hull] | None = None, root_name: str = "Scene Root",
              *, material_specs: list[MaterialSpec | None] | None = None) -> bytes:
    """Serialise Mesh IR (+ optional collision hulls) to Skyrim SSE NIF bytes.

    `material_specs` is keyword-only and optional by design: the five positional
    parameters above are a frozen cross-process contract (darksouls-port calls this
    build as a subprocess). When it is None -- or every entry is None -- the output
    is byte-for-byte what it has always been. Entries align positionally with
    `meshes`; a None entry means "that shape keeps the static defaults".
    """
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
        i = len(shape_refs)
        spec = material_specs[i] if material_specs and i < len(material_specs) else None
        # NiAlphaProperty goes BEFORE its BSTriShape, next to the LSP/texset group.
        # NIF refs may point either way, but the engine loads blocks sequentially and
        # every vanilla file we checked keeps a shape's properties ahead of it; the
        # bhk chain above is bottom-up for the same reason.
        alpha = _alpha_settings(spec)
        alpha_idx = -1
        if alpha is not None:
            alpha_idx = len(blocks)
            blocks.append(("NiAlphaProperty", _build_alpha_property(0, alpha[0], alpha[1])))
        shape_idx = len(blocks)
        lsp_idx = shape_idx + 1
        texset_idx = shape_idx + 2
        name_idx = len(strings)
        strings.append(m.name or f"shape_{shape_idx}")
        has_n = normal_map_flags[i] if normal_map_flags else False
        blocks.append(("BSTriShape", _build_bstrishape(name_idx, lsp_idx, m, alpha_idx)))
        blocks.append(("BSLightingShaderProperty", _build_lsp(
            0, texset_idx, spec, has_vertex_colors=m.has_colors)))
        blocks.append(("BSShaderTextureSet",
                       _build_texset(_slot_paths(m, texprefix, has_n, spec))))
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

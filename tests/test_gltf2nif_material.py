"""glTF PBR material -> BSLightingShaderProperty / NiAlphaProperty mapping.

Two jobs:
  1. Prove the backward-compat gate: without `material_specs` the writer's bytes are
     EXACTLY what they have always been (pinned by sha256, and by equality against
     the None / [None]*n spellings). darksouls-port calls this build as a subprocess,
     so a byte drift there is a live breakage.
  2. Verify each mapping, both by reading the NIF back through nif2gltf and by
     asserting on raw byte offsets inside the BSLightingShaderProperty block.
"""

from __future__ import annotations

import hashlib
import inspect
import struct

import pytest

from gltf2nif.geometry import Mesh
from gltf2nif.material import (
    MaterialSpec,
    metallic_specular_color,
    metallic_to_specular_strength,
    read_materials,
    roughness_to_glossiness,
    specs_for_meshes,
)
from gltf2nif.nif_writer import _slot_paths, build_nif
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif

TEXPREFIX = "textures\\dsport"

# sha256 of the writer's output for the fixtures below, captured from the tree BEFORE
# material support existed. These two digests are the backward-compatibility contract:
# if either changes, the default (spec-less) NIF bytes moved and downstream ports break.
BASELINE_SINGLE_SHA = "13f5b8bcd63d2a399360a24486250715d173c018a01cf1d1b34223e48f21b742"
BASELINE_DOUBLE_SHA = "8f43c46ea2fc94e7f316b5f4257d41f2e9b342150860bd8fd184f2e6d8afc8d9"


def _tri() -> Mesh:
    return Mesh(name="tri",
                positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                normals=[(0.0, 0.0, 1.0)] * 3,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                triangles=[(0, 1, 2)], material="mywall")


def _quad() -> Mesh:
    return Mesh(name="quad",
                positions=[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
                normals=[(0.0, 1.0, 0.0)] * 3,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                triangles=[(0, 1, 2)], material="floor")


def _header(data: bytes) -> dict:
    return _read_header(_Reader(data))


def _block_offsets(data: bytes, wanted: str) -> list[int]:
    hdr = _header(data)
    return [off for off, t in zip(hdr["offsets"], hdr["types"]) if t == wanted]


def _lsp_floats(data: bytes, field_offset: int, count: int = 1, index: int = 0):
    base = _block_offsets(data, "BSLightingShaderProperty")[index]
    return struct.unpack_from(f"<{count}f", data, base + field_offset)


def _lsp_u32(data: bytes, field_offset: int, index: int = 0) -> int:
    base = _block_offsets(data, "BSLightingShaderProperty")[index]
    return struct.unpack_from("<I", data, base + field_offset)[0]


# ------------------------------------------------------------------ invariants

def test_build_nif_positional_signature_is_frozen():
    """The five positional params are a cross-process contract; specs must be kw-only."""
    sig = inspect.signature(build_nif)
    positional = [(n, p.default) for n, p in sig.parameters.items()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    assert positional == [
        ("meshes", inspect.Parameter.empty),
        ("texprefix", inspect.Parameter.empty),
        ("normal_map_flags", inspect.Parameter.empty),
        ("hulls", None),
        ("root_name", "Scene Root"),
    ]
    specs = sig.parameters["material_specs"]
    assert specs.kind is inspect.Parameter.KEYWORD_ONLY
    assert specs.default is None


def test_mesh_material_index_is_appended_with_a_default():
    fields = list(Mesh.__dataclass_fields__)
    assert fields[-1] == "material_index"
    assert Mesh().material_index == -1


def test_omitting_specs_equals_none_equals_all_none_single_shape():
    meshes = [_tri()]
    plain = build_nif(meshes, TEXPREFIX, [True])
    explicit_none = build_nif(meshes, TEXPREFIX, [True], material_specs=None)
    all_none = build_nif(meshes, TEXPREFIX, [True], material_specs=[None])
    assert plain == explicit_none == all_none
    assert hashlib.sha256(plain).hexdigest() == BASELINE_SINGLE_SHA


def test_omitting_specs_equals_all_none_two_shapes():
    meshes = [_tri(), _quad()]
    plain = build_nif(meshes, TEXPREFIX, [True, False])
    all_none = build_nif(meshes, TEXPREFIX, [True, False], material_specs=[None, None])
    assert plain == all_none
    assert hashlib.sha256(plain).hexdigest() == BASELINE_DOUBLE_SHA


def test_empty_spec_list_and_short_spec_list_are_tolerated():
    meshes = [_tri(), _quad()]
    plain = build_nif(meshes, TEXPREFIX, [True, False])
    assert build_nif(meshes, TEXPREFIX, [True, False], material_specs=[]) == plain
    # A short list leaves the trailing shapes on the defaults.
    short = build_nif(meshes, TEXPREFIX, [True, False], material_specs=[None])
    assert short == plain


def test_default_path_emits_no_alpha_property():
    data = build_nif([_tri()], TEXPREFIX, [True])
    assert "NiAlphaProperty" not in _header(data)["types"]


# ------------------------------------------------------------------- formulas

@pytest.mark.parametrize("roughness,expected", [
    (1.0, 2.0),                 # fully rough -> matte floor
    (0.5, 2.0 ** 5.5),          # ~45.25
    (0.0, 999.0),               # 2**10 = 1024, clamped
])
def test_roughness_to_glossiness(roughness, expected):
    assert roughness_to_glossiness(roughness) == pytest.approx(expected, rel=1e-6)


def test_roughness_to_glossiness_clamps_out_of_range_input():
    assert roughness_to_glossiness(-5.0) == 999.0
    assert roughness_to_glossiness(7.0) == 2.0


@pytest.mark.parametrize("metallic,factor,expected", [
    (0.0, 1.0, 0.2),
    (0.5, 1.0, 0.6),
    (1.0, 1.0, 1.0),
    (1.0, 0.5, 0.5),   # KHR_materials_specular scales the ramp
])
def test_metallic_to_specular_strength(metallic, factor, expected):
    assert metallic_to_specular_strength(metallic, factor) == pytest.approx(expected)


def test_metallic_specular_color_lerps_white_to_base():
    assert metallic_specular_color((0.2, 0.4, 0.6, 1.0), 0.0) == pytest.approx((1, 1, 1))
    assert metallic_specular_color((0.2, 0.4, 0.6, 1.0), 1.0) == pytest.approx((0.2, 0.4, 0.6))
    assert metallic_specular_color((0.2, 0.4, 0.6, 1.0), 0.5) == pytest.approx((0.6, 0.7, 0.8))


# ------------------------------------------------------- LSP field byte offsets

def _spec(**kw) -> MaterialSpec:
    kw.setdefault("base_name", "mywall")
    return MaterialSpec(**kw)


def test_emissive_colour_and_multiple_reach_the_lsp():
    spec = _spec(emissive=(0.25, 0.5, 0.75), emissive_strength=3.0)
    data = build_nif([_tri()], TEXPREFIX, [True], material_specs=[spec])
    assert _lsp_floats(data, 44, 3) == pytest.approx((0.25, 0.5, 0.75))
    assert _lsp_floats(data, 56)[0] == pytest.approx(3.0)


def test_base_colour_alpha_reaches_the_lsp_alpha_field():
    spec = _spec(base_color=(1.0, 1.0, 1.0, 0.4))
    data = build_nif([_tri()], TEXPREFIX, [True], material_specs=[spec])
    assert _lsp_floats(data, 64)[0] == pytest.approx(0.4)


def test_roughness_reaches_glossiness_and_metallic_reaches_specular():
    spec = _spec(base_color=(0.2, 0.4, 0.6, 1.0), metallic=0.5, roughness=0.5)
    data = build_nif([_tri()], TEXPREFIX, [True], material_specs=[spec])
    assert _lsp_floats(data, 72)[0] == pytest.approx(2.0 ** 5.5, rel=1e-6)
    assert _lsp_floats(data, 76, 3) == pytest.approx((0.6, 0.7, 0.8))
    assert _lsp_floats(data, 88)[0] == pytest.approx(0.6)


def test_double_sided_flag_follows_the_spec_but_defaults_on_without_one():
    ds = build_nif([_tri()], TEXPREFIX, [True], material_specs=[_spec(double_sided=True)])
    ss = build_nif([_tri()], TEXPREFIX, [True], material_specs=[_spec(double_sided=False)])
    legacy = build_nif([_tri()], TEXPREFIX, [True])
    assert _lsp_u32(ds, 20) & 0x10
    assert not _lsp_u32(ss, 20) & 0x10
    assert _lsp_u32(legacy, 20) & 0x10        # spec-less path stays force-double-sided
    assert _lsp_u32(ss, 20) == 0x00008021


def test_specular_flag_drops_only_for_rough_dielectrics():
    rough = build_nif([_tri()], TEXPREFIX, [True],
                      material_specs=[_spec(metallic=0.0, roughness=1.0)])
    shiny = build_nif([_tri()], TEXPREFIX, [True],
                      material_specs=[_spec(metallic=0.0, roughness=0.3)])
    metal = build_nif([_tri()], TEXPREFIX, [True],
                      material_specs=[_spec(metallic=1.0, roughness=1.0)])
    assert not _lsp_u32(rough, 16) & 0x1
    assert _lsp_u32(shiny, 16) & 0x1
    assert _lsp_u32(metal, 16) & 0x1
    # Model_Space_Normals (0x1000) and Vertex_Alpha (0x8) are never touched.
    for data in (rough, shiny, metal):
        assert not _lsp_u32(data, 16) & 0x1000
        assert not _lsp_u32(data, 16) & 0x8


# ------------------------------------------------------------- texture slots

def test_slot_paths_add_glow_and_specular_only_when_declared():
    mesh = _tri()
    assert _slot_paths(mesh, TEXPREFIX, True) == [
        "textures\\dsport\\mywall.dds", "textures\\dsport\\mywall_n.dds"]
    plain_spec = _spec()
    assert _slot_paths(mesh, TEXPREFIX, True, plain_spec)[2] == ""
    assert _slot_paths(mesh, TEXPREFIX, True, plain_spec)[7] == ""
    full = _slot_paths(mesh, TEXPREFIX, True,
                       _spec(has_specular_map=True, has_emissive_map=True))
    assert full[0] == "textures\\dsport\\mywall.dds"
    assert full[1] == "textures\\dsport\\mywall_n.dds"
    assert full[2] == "textures\\dsport\\mywall_g.dds"
    assert full[7] == "textures\\dsport\\mywall_s.dds"
    assert full[3:7] == [""] * 4


def test_specular_slot_reaches_the_texture_set_bytes():
    data = build_nif([_tri()], TEXPREFIX, [True],
                     material_specs=[_spec(has_specular_map=True, has_emissive_map=True)])
    assert b"mywall_s.dds" in data
    assert b"mywall_g.dds" in data
    # A spec with no extra maps must not invent slots.
    plain = build_nif([_tri()], TEXPREFIX, [True], material_specs=[_spec()])
    assert b"mywall_s.dds" not in plain
    assert b"mywall_g.dds" not in plain
    # A default MaterialSpec is NOT "no spec": it says single-sided + fully rough, so
    # the shader flags/glossiness legitimately differ from the spec-less constants.
    # Only the absence of specs is byte-frozen (see the invariant tests above).
    assert plain != build_nif([_tri()], TEXPREFIX, [True])


def test_explicit_normal_texture_name_overrides_probe_flag_and_derived_name():
    explicit = r"textures\DsPort\tex\mywall_n__wet.dds"
    spec = _spec(normal_texture_name=explicit)
    slots = _slot_paths(_tri(), TEXPREFIX, False, spec)
    assert slots[1] == explicit
    data = build_nif([_tri()], TEXPREFIX, [False], material_specs=[spec])
    assert explicit.encode() in data
    assert b"textures\\dsport\\mywall_n.dds" not in data


def test_empty_normal_texture_name_preserves_existing_slot_logic():
    spec = _spec(normal_texture_name="")
    assert _slot_paths(_tri(), TEXPREFIX, True, spec)[1] == (
        r"textures\dsport\mywall_n.dds"
    )
    assert _slot_paths(_tri(), TEXPREFIX, False, spec)[1] == ""


# ---------------------------------------------------------- NiAlphaProperty

def _alpha_block(data: bytes) -> tuple[int, int]:
    off = _block_offsets(data, "NiAlphaProperty")[0]
    flags = struct.unpack_from("<H", data, off + 12)[0]
    threshold = data[off + 14]
    return flags, threshold


def _shape_alpha_ref(data: bytes) -> int:
    """BSTriShape Alpha Property ref: the i32 right after the Shader Property ref."""
    off = _block_offsets(data, "BSTriShape")[0]
    # Name u32, NumExtra u32, Controller i32, Flags u32, Translation 3f, Rotation 9f,
    # Scale f, Collision i32, Sphere 4f, Skin i32, Shader i32, Alpha i32
    alpha_at = off + 4 + 4 + 4 + 4 + 12 + 36 + 4 + 4 + 16 + 4 + 4
    return struct.unpack_from("<i", data, alpha_at)[0]


def test_blend_material_emits_a_nialphaproperty():
    data = build_nif([_tri()], TEXPREFIX, [True],
                     material_specs=[_spec(alpha_mode="BLEND")])
    hdr = _header(data)
    assert "NiAlphaProperty" in hdr["types"]
    flags, threshold = _alpha_block(data)
    assert flags == 0x00ED
    assert threshold == 0
    assert _shape_alpha_ref(data) != -1


def test_mask_material_encodes_the_cutoff_as_the_threshold():
    data = build_nif([_tri()], TEXPREFIX, [True],
                     material_specs=[_spec(alpha_mode="MASK", alpha_cutoff=0.5)])
    flags, threshold = _alpha_block(data)
    assert flags == 0x0201
    assert threshold == 128  # round(0.5 * 255)
    data2 = build_nif([_tri()], TEXPREFIX, [True],
                      material_specs=[_spec(alpha_mode="MASK", alpha_cutoff=1.0)])
    assert _alpha_block(data2)[1] == 255


def test_opaque_material_emits_no_alpha_property_and_keeps_ref_minus_one():
    data = build_nif([_tri()], TEXPREFIX, [True], material_specs=[_spec()])
    assert "NiAlphaProperty" not in _header(data)["types"]
    assert _shape_alpha_ref(data) == -1


def test_alpha_property_ref_points_at_a_real_alpha_block():
    data = build_nif([_tri()], TEXPREFIX, [True],
                     material_specs=[_spec(alpha_mode="BLEND")])
    hdr = _header(data)
    ref = _shape_alpha_ref(data)
    assert hdr["types"][ref] == "NiAlphaProperty"
    # It sits immediately before its shape, with the shape's own refs still intact.
    shape_index = hdr["types"].index("BSTriShape")
    assert ref == shape_index - 1
    assert hdr["types"][shape_index + 1] == "BSLightingShaderProperty"
    assert hdr["types"][shape_index + 2] == "BSShaderTextureSet"


def test_mixed_shapes_keep_their_own_alpha_blocks_and_root_children():
    specs = [_spec(alpha_mode="BLEND"), None, _spec(base_name="floor", alpha_mode="MASK")]
    meshes = [_tri(), _quad(), _quad()]
    data = build_nif(meshes, TEXPREFIX, [True, False, False], material_specs=specs)
    hdr = _header(data)
    assert hdr["types"].count("NiAlphaProperty") == 2
    assert hdr["types"].count("BSTriShape") == 3
    # The root still reaches all three shapes: nif2gltf resolves children + textures.
    read_back = read_nif(data)
    assert len(read_back) == 3


def test_alpha_material_still_round_trips_through_nif2gltf():
    spec = _spec(alpha_mode="BLEND", base_color=(1.0, 1.0, 1.0, 0.25))
    data = build_nif([_tri()], TEXPREFIX, [True], material_specs=[spec])
    meshes = read_nif(data)
    assert len(meshes) == 1
    assert [tuple(t) for t in meshes[0].triangles] == [(0, 1, 2)]


# ------------------------------------------------------------ glTF extraction

def _two_material_gltf(tmp_path):
    from pygltflib import GLTF2, PbrMetallicRoughness

    from tests.gltf2nif_fixtures import write_gltf_interleaved

    prim_a = {"positions": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
              "normals": [(0, 0, 1)] * 3, "uvs": [(0, 0), (1, 0), (0, 1)],
              "triangles": [(0, 1, 2)], "material": "wall_a.tga"}
    prim_b = {"positions": [(0, 0, 0), (2, 0, 0), (0, 2, 0)],
              "normals": [(0, 1, 0)] * 3, "uvs": [(0, 0), (1, 0), (0, 1)],
              "triangles": [(0, 1, 2)], "material": "wall_b.tga"}
    path = write_gltf_interleaved(str(tmp_path / "two.gltf"), [prim_a, prim_b])

    gltf = GLTF2().load(path)
    gltf.materials[0].pbrMetallicRoughness = PbrMetallicRoughness(
        baseColorFactor=[0.1, 0.2, 0.3, 0.4], metallicFactor=0.25, roughnessFactor=0.75)
    gltf.materials[0].alphaMode = "BLEND"
    gltf.materials[0].doubleSided = True
    gltf.materials[0].emissiveFactor = [0.5, 0.25, 0.125]
    gltf.materials[0].extensions = {
        "KHR_materials_emissive_strength": {"emissiveStrength": 4.0},
        "KHR_materials_specular": {"specularFactor": 0.5,
                                   "specularTexture": {"index": 0}},
    }
    gltf.materials[1].pbrMetallicRoughness = PbrMetallicRoughness(
        baseColorFactor=[1.0, 1.0, 1.0, 1.0], metallicFactor=0.0, roughnessFactor=1.0)
    gltf.materials[1].alphaMode = "MASK"
    gltf.materials[1].alphaCutoff = 0.25
    gltf.save_json(path)
    return path


def test_read_materials_reads_factors_and_khr_extensions(tmp_path):
    from pygltflib import GLTF2

    gltf = GLTF2().load(_two_material_gltf(tmp_path))
    specs = read_materials(gltf)
    assert len(specs) == 2
    a, b = specs
    assert a.base_name == "wall_a"          # source extension stripped
    assert a.base_color == pytest.approx((0.1, 0.2, 0.3, 0.4))
    assert a.metallic == pytest.approx(0.25)
    assert a.roughness == pytest.approx(0.75)
    assert a.emissive == pytest.approx((0.5, 0.25, 0.125))
    assert a.emissive_strength == pytest.approx(4.0)
    assert a.specular_factor == pytest.approx(0.5)
    assert a.has_specular_map is True
    assert a.alpha_mode == "BLEND"
    assert a.double_sided is True
    assert b.alpha_mode == "MASK"
    assert b.alpha_cutoff == pytest.approx(0.25)
    assert b.has_specular_map is False
    assert b.specular_factor == 1.0          # extension absent -> neutral default
    assert b.emissive_strength == 1.0


def test_read_materials_survives_a_material_with_nothing_set(tmp_path):
    from pygltflib import GLTF2, Material

    gltf = GLTF2(materials=[Material(name="bare")])
    spec = read_materials(gltf)[0]
    assert spec.base_name == "bare"
    assert spec.base_color == (1.0, 1.0, 1.0, 1.0)
    assert spec.metallic == 1.0 and spec.roughness == 1.0
    assert spec.alpha_mode == "OPAQUE" and spec.alpha_cutoff == 0.5
    assert spec.has_normal_map is False and spec.has_emissive_map is False


def test_read_materials_ignores_a_junk_extension_payload():
    from pygltflib import GLTF2, Material

    material = Material(name="odd")
    material.extensions = {"KHR_materials_emissive_strength": "not-a-dict",
                           "KHR_materials_specular": {"specularFactor": "nope"}}
    spec = read_materials(GLTF2(materials=[material]))[0]
    assert spec.emissive_strength == 1.0
    assert spec.specular_factor == 1.0


def test_specs_for_meshes_aligns_with_read_gltf_order(tmp_path):
    from gltf2nif.gltf_reader import read_gltf

    path = _two_material_gltf(tmp_path)
    meshes = read_gltf(path)
    assert [m.material for m in meshes] == ["wall_a", "wall_b"]
    assert [m.material_index for m in meshes] == [0, 1]

    specs = specs_for_meshes(path, meshes)
    assert len(specs) == len(meshes)
    assert [s.base_name for s in specs] == ["wall_a", "wall_b"]
    assert specs[0].alpha_mode == "BLEND"
    assert specs[1].alpha_mode == "MASK"


def test_specs_for_meshes_falls_back_to_a_unique_name_match(tmp_path):
    path = _two_material_gltf(tmp_path)
    meshes = [Mesh(name="x", material="wall_b"), Mesh(name="y", material="nope")]
    specs = specs_for_meshes(path, meshes)
    assert specs[0] is not None and specs[0].base_name == "wall_b"
    assert specs[1] is None


def test_specs_for_meshes_end_to_end_drives_the_writer(tmp_path):
    from gltf2nif.gltf_reader import read_gltf

    path = _two_material_gltf(tmp_path)
    meshes = read_gltf(path)
    specs = specs_for_meshes(path, meshes)
    data = build_nif(meshes, TEXPREFIX, [False, False], material_specs=specs)
    hdr = _header(data)
    assert hdr["types"].count("NiAlphaProperty") == 2
    # shape 0 (wall_a): BLEND, double sided, emissive 4x
    assert _lsp_floats(data, 56, index=0)[0] == pytest.approx(4.0)
    assert _lsp_floats(data, 64, index=0)[0] == pytest.approx(0.4)
    assert _lsp_u32(data, 20, index=0) & 0x10
    # (0.2 + 0.8*0.25) * 0.5 = 0.2
    assert _lsp_floats(data, 88, index=0)[0] == pytest.approx(0.2)
    # shape 1 (wall_b): MASK cutoff 0.25, fully rough dielectric -> Specular dropped
    assert not _lsp_u32(data, 16, index=1) & 0x1
    assert not _lsp_u32(data, 20, index=1) & 0x10
    off = _block_offsets(data, "NiAlphaProperty")[1]
    assert struct.unpack_from("<H", data, off + 12)[0] == 0x0201
    assert data[off + 14] == 64  # round(0.25 * 255)

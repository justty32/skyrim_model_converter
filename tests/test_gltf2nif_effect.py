"""BSEffectShaderProperty emission for additive/effect materials."""

from __future__ import annotations

import struct

import pytest

from gltf2nif.geometry import Mesh
from gltf2nif.material import MaterialSpec
from gltf2nif.nif_writer import build_nif
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header


def _mesh() -> Mesh:
    return Mesh(
        name="lightshaft",
        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        normals=[(0.0, 0.0, 1.0)] * 3,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        triangles=[(0, 1, 2)],
        material="shaft",
    )


def _header(data: bytes) -> dict:
    return _read_header(_Reader(data))


def test_effect_kind_emits_effect_property_without_lighting_texture_set():
    spec = MaterialSpec(shader_kind="effect")
    data = build_nif([_mesh()], r"textures\dsport", [False], material_specs=[spec])
    header = _header(data)

    assert header["types"].count("BSEffectShaderProperty") == 1
    assert "BSLightingShaderProperty" not in header["types"]
    assert "BSShaderTextureSet" not in header["types"]
    shape_index = header["types"].index("BSTriShape")
    effect_index = header["types"].index("BSEffectShaderProperty")
    shape_offset = header["offsets"][shape_index]
    assert struct.unpack_from("<i", data, shape_offset + 92)[0] == effect_index


def test_effect_property_uses_12_byte_niobjectnet_prefix():
    data = build_nif([_mesh()], r"textures\dsport", [False],
                     material_specs=[MaterialSpec(shader_kind="effect")])
    header = _header(data)
    offset = header["offsets"][header["types"].index("BSEffectShaderProperty")]

    # Decode the on-disk prefix directly.  Unlike BSLightingShaderProperty, an
    # effect property has no leading Shader Type field: its inherited NiObjectNET
    # fields are Name, Num Extra Data List, Controller.  An invalid huge list count
    # makes Skyrim allocate/copy an equally invalid amount before shader data.
    assert struct.unpack_from("<iIi", data, offset) == (-1, 0, -1)


def test_effect_property_bs100_field_layout_and_values():
    spec = MaterialSpec(shader_kind="effect", base_color=(0.25, 0.5, 0.75, 0.4),
                        emissive_strength=2.5)
    data = build_nif([_mesh()], r"textures\dsport", [False], material_specs=[spec])
    header = _header(data)
    index = header["types"].index("BSEffectShaderProperty")
    offset = header["offsets"][index]

    assert struct.unpack_from("<II", data, offset + 12) == (0x80000000, 0x20)
    assert struct.unpack_from("<4f", data, offset + 20) == pytest.approx((0, 0, 1, 1))
    source_len = struct.unpack_from("<I", data, offset + 36)[0]
    source_start = offset + 40
    source_end = source_start + source_len
    assert data[source_start:source_end].decode() == r"textures\dsport\shaft.dds"
    assert data[source_end:source_end + 4] == bytes((3, 255, 0, 0))
    assert struct.unpack_from("<4f", data, source_end + 4) == pytest.approx((1, 1, 1, 0))
    assert struct.unpack_from("<4f", data, source_end + 20) == pytest.approx(
        (0.25, 0.5, 0.75, 0.4)
    )
    assert struct.unpack_from("<2f", data, source_end + 36) == pytest.approx((2.5, 100.0))
    assert struct.unpack_from("<I", data, source_end + 44)[0] == 0
    assert offset + header["block_sizes"][index] == source_end + 48


def test_lighting_remains_the_default_shader_kind():
    data = build_nif([_mesh()], r"textures\dsport", [False],
                     material_specs=[MaterialSpec()])
    assert "BSLightingShaderProperty" in _header(data)["types"]
    assert "BSEffectShaderProperty" not in _header(data)["types"]


def test_blended_effect_enables_vertex_alpha_shader_flag():
    spec = MaterialSpec(shader_kind="effect", alpha_mode="BLEND")
    data = build_nif([_mesh()], r"textures\dsport", [False], material_specs=[spec])
    header = _header(data)
    offset = header["offsets"][header["types"].index("BSEffectShaderProperty")]
    assert struct.unpack_from("<I", data, offset + 12)[0] & 0x8


def test_double_sided_effect_enables_shader_flag():
    spec = MaterialSpec(shader_kind="effect", double_sided=True)
    data = build_nif([_mesh()], r"textures\dsport", [False], material_specs=[spec])
    header = _header(data)
    offset = header["offsets"][header["types"].index("BSEffectShaderProperty")]
    assert struct.unpack_from("<I", data, offset + 16)[0] & 0x10


def test_colored_effect_uses_nif_schema_vertex_color_bits():
    mesh = _mesh()
    mesh.colors = [(1, 0, 0, 1), (0, 1, 0, .5), (0, 0, 1, 0)]
    data = build_nif([mesh], r"textures\test", [False],
                     material_specs=[MaterialSpec(shader_kind="effect")])
    header = _header(data)
    shape = header["offsets"][header["types"].index("BSTriShape")]
    effect = header["offsets"][header["types"].index("BSEffectShaderProperty")]
    desc, = struct.unpack_from("<Q", data, shape + 100)
    assert (desc >> 44) & 0x20  # nif.xml VertexAttribute.Vertex_Colors.
    assert not (desc >> 44) & 0x200  # Instance.
    flags2, = struct.unpack_from("<I", data, effect + 16)
    assert flags2 & 0x20  # SkyrimShaderPropertyFlags2.Vertex_Colors.
    assert not flags2 & 0x80  # Assume_Shadowmask.

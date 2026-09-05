"""any2nif.textures.export_textures: glTF/GLB material images -> Skyrim .dds slots."""

from __future__ import annotations

import os

import numpy as np
import pytest
from pygltflib import GLTF2, Primitive

from any2nif.textures import export_textures
from gltf2nif.gltf_reader import _material_basename as gltf2nif_basename
from tests.tex2dds_fixtures import (
    build_glb,
    build_gltf_with_data_uri,
    build_gltf_with_external_images,
    decode_dds,
    flat_rgb,
    gradient_rgb,
    gradient_rgba,
)
from tex2dds import parse_header


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------- slot mapping

def test_all_three_slots_are_written(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{
        "name": "stone",
        "diffuse": gradient_rgb(32, 32),
        "normal": flat_rgb(16, (128, 200, 255)),
        "metallicRoughness": flat_rgb(16, (0, 200, 0)),
    }])
    out = str(tmp_path / "textures")
    result = export_textures(glb, out)
    assert result == {"stone": {
        "diffuse": "stone.dds", "normal": "stone_n.dds", "specular": "stone_s.dds"}}
    for name in result["stone"].values():
        assert os.path.isfile(os.path.join(out, name))


def test_returned_names_are_relative_to_out_dir(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "iron", "diffuse": gradient_rgb(16, 16)}])
    out = str(tmp_path / "tex")
    result = export_textures(glb, out)
    name = result["iron"]["diffuse"]
    assert not os.path.isabs(name)
    assert os.path.dirname(name) == ""
    assert os.path.isfile(os.path.join(out, name))


def test_missing_slots_are_absent_from_the_dict(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "plain", "diffuse": gradient_rgb(16, 16)}])
    result = export_textures(glb, str(tmp_path / "out"))
    assert result == {"plain": {"diffuse": "plain.dds"}}


def test_material_without_any_texture_is_omitted(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "bare"}])
    assert export_textures(glb, str(tmp_path / "out")) == {}


def test_multiple_materials_each_get_their_own_files(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [
        {"name": "wall", "diffuse": gradient_rgb(16, 16)},
        {"name": "floor", "diffuse": gradient_rgb(16, 16), "normal": flat_rgb(8, (128, 128, 255))},
    ])
    out = str(tmp_path / "out")
    result = export_textures(glb, out)
    assert set(result) == {"wall", "floor"}
    assert set(result["floor"]) == {"diffuse", "normal"}
    assert sorted(os.listdir(out)) == ["floor.dds", "floor_n.dds", "wall.dds"]


# ----------------------------------------------------------------- base name contract

def test_extension_is_stripped_from_the_material_name(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "m18_wall_07.tga",
                                               "diffuse": gradient_rgb(16, 16)}])
    result = export_textures(glb, str(tmp_path / "out"))
    assert result == {"m18_wall_07": {"diffuse": "m18_wall_07.dds"}}


@pytest.mark.parametrize("name", ["stone", "m18_wall_07.tga", "brick.dds", "a.b.png", "plain"])
def test_base_name_agrees_with_gltf2nif(tmp_path, name):
    """The NIF's texture slots are named by gltf2nif; if these two ever diverged,
    the exported .dds files would not line up with the paths inside the .nif."""
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": name, "diffuse": gradient_rgb(8, 8)}])
    gltf = GLTF2().load(glb)
    expected = gltf2nif_basename(gltf, Primitive(material=0))
    result = export_textures(glb, str(tmp_path / "out"))
    assert list(result) == [expected]
    assert result[expected]["diffuse"] == f"{expected}.dds"


def test_unnamed_material_is_skipped(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [
        {"name": None, "diffuse": gradient_rgb(16, 16)},
        {"name": "", "diffuse": gradient_rgb(16, 16)},
        {"name": "kept", "diffuse": gradient_rgb(16, 16)},
    ])
    assert list(export_textures(glb, str(tmp_path / "out"))) == ["kept"]


# --------------------------------------------------------------------- image sources

def test_external_uri_image(tmp_path):
    gltf = build_gltf_with_external_images(str(tmp_path), "wood", gradient_rgb(16, 16))
    result = export_textures(gltf, str(tmp_path / "out"))
    assert result == {"wood": {"diffuse": "wood.dds"}}


def test_data_uri_image(tmp_path):
    gltf = build_gltf_with_data_uri(str(tmp_path), "glass", gradient_rgba(16, 16))
    result = export_textures(gltf, str(tmp_path / "out"))
    assert result == {"glass": {"diffuse": "glass.dds"}}
    # The source has a real alpha ramp -> auto must pick BC3.
    assert parse_header(_read(str(tmp_path / "out" / "glass.dds")))["fourcc"] == b"DXT5"


def test_glb_buffer_view_image(tmp_path):
    """The GLB path: the PNG lives in the binary blob, not in a URI."""
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "blob", "diffuse": gradient_rgb(32, 32)}])
    out = str(tmp_path / "out")
    result = export_textures(glb, out)
    back = decode_dds(_read(os.path.join(out, result["blob"]["diffuse"])))
    assert back.shape == (32, 32, 4)
    assert np.sqrt(((back[:, :, :3] - gradient_rgb(32, 32)) ** 2).mean()) < 6.0


# ------------------------------------------------------------------- slot semantics

def test_normal_slot_is_bc3_with_flipped_green(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "n", "normal": flat_rgb(16, (128, 200, 255))}])
    out = str(tmp_path / "out")
    export_textures(glb, out)
    data = _read(os.path.join(out, "n_n.dds"))
    assert parse_header(data)["fourcc"] == b"DXT5"
    back = decode_dds(data)
    assert abs(int(back[0, 0, 1]) - (255 - 200)) <= 4
    assert back[0, 0, 3] == 255  # glossiness filled in when the source has none


def test_specular_slot_inverts_gltf_roughness(tmp_path):
    """glTF roughness (G) -> Skyrim specular is approximated as 255 - G."""
    glb = build_glb(str(tmp_path / "s.glb"),
                    [{"name": "r", "metallicRoughness": flat_rgb(16, (0, 200, 0))}])
    out = str(tmp_path / "out")
    export_textures(glb, out)
    data = _read(os.path.join(out, "r_s.dds"))
    assert parse_header(data)["fourcc"] == b"DXT1"
    back = decode_dds(data)
    for channel in range(3):
        assert abs(int(back[0, 0, channel]) - (255 - 200)) <= 4


def test_khr_specular_extension_wins_over_metallic_roughness(tmp_path):
    """A real specular signal beats the roughness approximation when both exist."""
    spec = np.zeros((16, 16, 4), dtype=np.uint8)
    spec[:, :, :3] = (0, 0, 0)
    spec[:, :, 3] = 132  # specular strength lives in the alpha of specularTexture
    glb = build_glb(str(tmp_path / "s.glb"), [{
        "name": "k",
        "metallicRoughness": flat_rgb(16, (0, 200, 0)),
        "specular": spec,
    }])
    out = str(tmp_path / "out")
    export_textures(glb, out)
    back = decode_dds(_read(os.path.join(out, "k_s.dds")))
    assert abs(int(back[0, 0, 0]) - 132) <= 4
    assert abs(int(back[0, 0, 0]) - (255 - 200)) > 4  # i.e. not the roughness path


# -------------------------------------------------------------------------- options

def test_codec_applies_to_the_diffuse_slot(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "c", "diffuse": gradient_rgba(16, 16)}])
    auto_dir = str(tmp_path / "auto")
    forced_dir = str(tmp_path / "forced")
    export_textures(glb, auto_dir)
    export_textures(glb, forced_dir, codec="bc1")
    assert parse_header(_read(os.path.join(auto_dir, "c.dds")))["fourcc"] == b"DXT5"
    assert parse_header(_read(os.path.join(forced_dir, "c.dds")))["fourcc"] == b"DXT1"


def test_unknown_codec_raises(tmp_path):
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "c", "diffuse": gradient_rgb(8, 8)}])
    with pytest.raises(ValueError):
        export_textures(glb, str(tmp_path / "out"), codec="bc7")


def test_exported_files_carry_a_full_mip_chain(tmp_path):
    """Skyrim expects a complete chain on shipped textures."""
    glb = build_glb(str(tmp_path / "s.glb"), [{"name": "m", "diffuse": gradient_rgb(32, 32)}])
    out = str(tmp_path / "out")
    export_textures(glb, out)
    assert parse_header(_read(os.path.join(out, "m.dds")))["mip_count"] == 6

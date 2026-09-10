"""External and archived OBJ/COLLADA resources must not silently degrade to white materials."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST, texture_references
from tests.any2nif_fixtures import write_obj_textured


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _package_texture(output: Path) -> Path:
    manifest = json.loads((output / MANIFEST).read_text(encoding="utf-8"))
    nif = (output / manifest["mesh"]).read_bytes()
    diffuse = next(ref for ref in texture_references(nif) if ref.endswith(".dds"))
    return output / diffuse.replace("\\", "/")


def _write_textured_dae(path: Path, image_uri: str = "paint.png") -> Path:
    """Write a real COLLADA texture chain understood by pycollada."""
    path.write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit meter="1" name="meter"/><up_axis>Y_UP</up_axis></asset>
  <library_images><image id="paint-image"><init_from>{image_uri}</init_from></image></library_images>
  <library_effects><effect id="paint-effect"><profile_COMMON>
    <newparam sid="paint-surface"><surface type="2D"><init_from>paint-image</init_from></surface></newparam>
    <newparam sid="paint-sampler"><sampler2D><source>paint-surface</source></sampler2D></newparam>
    <technique sid="common"><phong><diffuse>
      <texture texture="paint-sampler" texcoord="UVSET0"/>
    </diffuse></phong></technique>
  </profile_COMMON></effect></library_effects>
  <library_materials><material id="paint-material" name="paint">
    <instance_effect url="#paint-effect"/>
  </material></library_materials>
  <library_geometries><geometry id="triangle" name="triangle"><mesh>
    <source id="positions"><float_array id="positions-array" count="9">0 0 0 1 0 0 0 1 0</float_array>
      <technique_common><accessor source="#positions-array" count="3" stride="3">
        <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
      </accessor></technique_common></source>
    <source id="normals"><float_array id="normals-array" count="9">0 0 1 0 0 1 0 0 1</float_array>
      <technique_common><accessor source="#normals-array" count="3" stride="3">
        <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
      </accessor></technique_common></source>
    <source id="uv"><float_array id="uv-array" count="6">0 0 1 0 0 1</float_array>
      <technique_common><accessor source="#uv-array" count="3" stride="2">
        <param name="S" type="float"/><param name="T" type="float"/>
      </accessor></technique_common></source>
    <vertices id="vertices"><input semantic="POSITION" source="#positions"/></vertices>
    <triangles count="1" material="paint-symbol">
      <input semantic="VERTEX" source="#vertices" offset="0"/>
      <input semantic="NORMAL" source="#normals" offset="1"/>
      <input semantic="TEXCOORD" source="#uv" offset="2" set="0"/>
      <p>0 0 0 1 1 1 2 2 2</p>
    </triangles>
  </mesh></geometry></library_geometries>
  <library_visual_scenes><visual_scene id="Scene"><node id="node">
    <instance_geometry url="#triangle"><bind_material><technique_common>
      <instance_material symbol="paint-symbol" target="#paint-material">
        <bind_vertex_input semantic="UVSET0" input_semantic="TEXCOORD" input_set="0"/>
      </instance_material>
    </technique_common></bind_material></instance_geometry>
  </node></visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#Scene"/></scene>
</COLLADA>
""", encoding="utf-8")
    return path


@pytest.mark.parametrize("damage", ["missing_png", "missing_mtl", "corrupt_png"])
def test_obj_broken_external_resource_is_exit_2_and_preserves_package(tmp_path, damage, capsys):
    source = Path(write_obj_textured(str(tmp_path / "model.obj")))
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    before = _snapshot(output)
    source_bytes = source.read_bytes()

    if damage == "missing_png":
        (tmp_path / "rock01.png").unlink()
    elif damage == "missing_mtl":
        (tmp_path / "material.mtl").unlink()
    else:
        (tmp_path / "rock01.png").write_bytes(b"this is not a PNG")

    capsys.readouterr()
    assert main([str(source), str(output), "--package"]) == 2
    resource = "material.mtl" if damage == "missing_mtl" else "rock01.png"
    assert resource in capsys.readouterr().err
    assert source.read_bytes() == source_bytes
    assert _snapshot(output) == before
    assert not list(tmp_path.glob(".any2nif-package-*"))


def test_obj_without_material_still_packages(tmp_path):
    source = Path(write_obj_textured(str(tmp_path / "plain.obj")))
    source.write_text(
        "\n".join(
            line for line in source.read_text(encoding="utf-8").splitlines()
            if not line.startswith(("mtllib ", "usemtl "))
        ) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "material.mtl").unlink()
    (tmp_path / "rock01.png").unlink()

    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    assert _package_texture(output).is_file()


def test_obj_relative_resource_paths_and_spaces_remain_supported(tmp_path):
    source = Path(write_obj_textured(str(tmp_path / "model with spaces.obj")))
    material = tmp_path / "materials" / "surface material.mtl"
    texture = tmp_path / "textures" / "red paint.png"
    material.parent.mkdir()
    texture.parent.mkdir()
    shutil.move(tmp_path / "material.mtl", material)
    shutil.move(tmp_path / "rock01.png", texture)
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "mtllib material.mtl", "mtllib materials/surface material.mtl"
        ),
        encoding="utf-8",
    )
    material.write_text(
        material.read_text(encoding="utf-8").replace(
            "map_Kd rock01.png", "map_Kd textures/red paint.png"
        ),
        encoding="utf-8",
    )

    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    with Image.open(_package_texture(output)) as image:
        image.load()
        red, green, blue = image.convert("RGB").getpixel((0, 0))
        assert red > 100
        assert green < 50 and blue < 50


def test_dae_external_texture_and_broken_resources(tmp_path, capsys):
    source = _write_textured_dae(tmp_path / "triangle.dae")
    Image.new("RGB", (4, 4), (30, 190, 60)).save(tmp_path / "paint.png")
    output = tmp_path / "data"
    args = [str(source), str(output), "--package", "--collision", "none"]

    assert main(args) == 0
    with Image.open(_package_texture(output)) as image:
        image.load()
        red, green, blue = image.convert("RGB").getpixel((0, 0))
        assert green > 100
        assert red < 60 and blue < 80
    before = _snapshot(output)

    (tmp_path / "paint.png").unlink()
    capsys.readouterr()
    assert main(args) == 2
    assert "paint.png" in capsys.readouterr().err
    assert _snapshot(output) == before

    (tmp_path / "paint.png").write_bytes(b"this is not a PNG")
    capsys.readouterr()
    assert main(args) == 2
    assert "paint.png" in capsys.readouterr().err
    assert _snapshot(output) == before


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_zae_internal_texture_failure_preserves_existing_package(tmp_path, damage, capsys):
    import io
    import zipfile

    dae = _write_textured_dae(tmp_path / "triangle.dae")
    pixels = io.BytesIO()
    Image.new("RGB", (4, 4), (30, 190, 60)).save(pixels, format="PNG")
    source = tmp_path / "model.zae"

    def archive(texture):
        with zipfile.ZipFile(source, "w") as output:
            output.writestr("triangle.dae", dae.read_bytes())
            if texture is not None:
                output.writestr("paint.png", texture)

    archive(pixels.getvalue())
    output = tmp_path / "Data"
    args = [str(source), str(output), "--package", "--collision", "none"]
    assert main(args) == 0
    with Image.open(_package_texture(output)) as image:
        red, green, blue = image.convert("RGB").getpixel((0, 0))
        assert green > 100 and red < 60 and blue < 80
    before = _snapshot(output)
    archive(None if damage == "missing" else b"invalid PNG")
    capsys.readouterr()
    assert main(args) == 2
    assert "paint.png" in capsys.readouterr().err
    assert _snapshot(output) == before

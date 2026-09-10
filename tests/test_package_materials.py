from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
from pathlib import Path

import pytest
from PIL import Image
from pygltflib import GLTF2

from any2nif.errors import AnyError
from any2nif.package_materials import prepare_materials
from gltf2nif import read_gltf


def _png(color=(128, 64, 32, 77)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), color).save(output, format="PNG")
    return output.getvalue()


def _uri(raw: bytes, mime="application/octet-stream") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _document(*, image_uri=None, image_buffer_view=False, materials=None,
              primitive_material=0):
    geometry = struct.pack("<9f3H", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2)
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0}, "indices": 1,
        }]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "buffers": [{"uri": _uri(geometry), "byteLength": len(geometry)}],
        "materials": materials or [],
    }
    if primitive_material is not None:
        doc["meshes"][0]["primitives"][0]["material"] = primitive_material
    if image_uri is not None:
        doc["images"] = [{"uri": image_uri}]
        doc["textures"] = [{"source": 0}]
    if image_buffer_view:
        png = _png()
        blob = geometry + png
        doc["buffers"] = [{"byteLength": len(blob)}]
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(geometry),
                                   "byteLength": len(png)})
        doc["images"] = [{"bufferView": 2, "mimeType": "image/png"}]
        doc["textures"] = [{"source": 0}]
        return doc, blob
    return doc, geometry


def _load_image(gltf, texture_index):
    image_index = gltf.textures[texture_index].source
    uri = gltf.images[image_index].uri
    raw = base64.b64decode(uri.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        return image.convert("RGBA")


def test_external_files_become_self_contained_and_source_is_unchanged(tmp_path):
    (tmp_path / "mesh.bin").write_bytes(b"\0" * 42)
    (tmp_path / "albedo.png").write_bytes(_png())
    material = {"name": "folder/unsafe.png", "doubleSided": True,
                "normalTexture": {"index": 0, "scale": 1.0},
                "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
    doc, _ = _document(image_uri="albedo.png", materials=[material])
    doc["buffers"][0] = {"uri": "mesh.bin", "byteLength": 42}
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    before = hashlib.sha256(source.read_bytes()).digest()

    output = tmp_path / "stage" / "prepared.gltf"
    assert prepare_materials(str(source), str(output)) == str(output)
    assert hashlib.sha256(source.read_bytes()).digest() == before
    result = GLTF2().load(str(output))
    assert result.materials[0].name == "material_0000"
    assert result.materials[0].doubleSided is True
    assert result.materials[0].normalTexture.scale == pytest.approx(1.0)
    assert all(buffer.uri.startswith("data:") for buffer in result.buffers)
    assert all(image.uri.startswith("data:image/") for image in result.images)
    assert len(read_gltf(str(output))) == 1


def test_names_are_unique_and_missing_material_gets_white_diffuse(tmp_path):
    materials = [{"name": "same.png"}, {"name": "same.png"}]
    doc, _ = _document(materials=materials, primitive_material=None)
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    result_path = prepare_materials(str(source), str(tmp_path / "prepared.gltf"))
    result = GLTF2().load(result_path)
    assert [m.name for m in result.materials] == [
        "material_0000", "material_0001", "material_0002"]
    assert result.meshes[0].primitives[0].material == 2
    image = _load_image(result, result.materials[2].pbrMetallicRoughness.baseColorTexture.index)
    assert image.size == (4, 4)
    assert image.getpixel((0, 0)) == (255, 255, 255, 255)


def test_base_color_factor_is_baked_in_linear_space_without_multiplying_alpha(tmp_path):
    material = {"name": "paint", "pbrMetallicRoughness": {
        "baseColorTexture": {"index": 0},
        "baseColorFactor": [0.5, 0.25, 1.0, 0.2],
    }}
    doc, _ = _document(image_uri=_uri(_png(), "image/png"), materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    result = GLTF2().load(prepare_materials(str(source), str(tmp_path / "out.gltf")))
    pbr = result.materials[0].pbrMetallicRoughness
    assert pbr.baseColorFactor == [0.5, 0.25, 1.0, 0.2]
    pixel = _load_image(result, pbr.baseColorTexture.index).getpixel((0, 0))
    assert pixel == pytest.approx((92, 30, 32, 77), abs=1)


def test_factor_only_material_gets_linear_to_srgb_diffuse(tmp_path):
    material = {"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.25, 0.0, 0.4]}}
    doc, _ = _document(materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    result = GLTF2().load(prepare_materials(str(source), str(tmp_path / "out.gltf")))
    pbr = result.materials[0].pbrMetallicRoughness
    assert pbr.baseColorFactor == [0.5, 0.25, 0.0, 0.4]
    assert _load_image(result, pbr.baseColorTexture.index).getpixel((0, 0)) == pytest.approx(
        (188, 137, 0, 255), abs=1)


def test_glb_buffer_view_image_is_supported(tmp_path):
    material = {"emissiveFactor": [0.1, 0.2, 0.3],
                "emissiveTexture": {"index": 0},
                "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
    doc, blob = _document(image_buffer_view=True, materials=[material])
    source = tmp_path / "source.glb"
    json_bytes = json.dumps(doc, separators=(",", ":")).encode()
    json_bytes += b" " * (-len(json_bytes) % 4)
    blob += b"\0" * (-len(blob) % 4)
    chunks = (struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes +
              struct.pack("<I4s", len(blob), b"BIN\0") + blob)
    source.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks)
    result = GLTF2().load(prepare_materials(str(source), str(tmp_path / "out.gltf")))
    assert result.materials[0].emissiveFactor == [0.1, 0.2, 0.3]
    assert result.materials[0].emissiveTexture.index == 0
    assert all(image.bufferView is None and image.uri.startswith("data:")
               for image in result.images)


@pytest.mark.parametrize("kind, expected_code", [("missing", 1), ("corrupt", 2)])
def test_missing_and_corrupt_specified_textures_fail_clearly(tmp_path, kind, expected_code):
    uri = "missing.png" if kind == "missing" else _uri(b"not an image", "image/png")
    material = {"normalTexture": {"index": 0}}
    doc, _ = _document(image_uri=uri, materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    with pytest.raises(AnyError) as caught:
        prepare_materials(str(source), str(tmp_path / "out.gltf"))
    assert caught.value.code == expected_code
    assert "image" in str(caught.value).lower()


def test_missing_texture_index_is_a_parse_error(tmp_path):
    material = {"normalTexture": {"index": 4}}
    doc, _ = _document(materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    with pytest.raises(AnyError) as caught:
        prepare_materials(str(source), str(tmp_path / "out.gltf"))
    assert caught.value.code == 2
    assert "missing texture 4" in str(caught.value)


@pytest.mark.parametrize("texture_info, message", [
    ({"index": 0, "texCoord": 1}, "TEXCOORD_1"),
    ({"index": 0, "extensions": {"KHR_texture_transform": {
        "offset": [0.25, 0.0],
    }}}, "missing UV accessor"),
])
def test_requested_uv_mapping_requires_an_accessor(
        tmp_path, texture_info, message):
    material = {"pbrMetallicRoughness": {"baseColorTexture": texture_info}}
    doc, _ = _document(image_uri=_uri(_png(), "image/png"), materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    with pytest.raises(AnyError) as caught:
        prepare_materials(str(source), str(tmp_path / "out.gltf"))
    assert caught.value.code == 2
    assert message in str(caught.value)


def test_identity_texture_transform_is_allowed_and_preserved(tmp_path):
    transform = {"offset": [0.0, 0.0], "scale": [1.0, 1.0],
                 "rotation": 0.0, "texCoord": 0}
    material = {"pbrMetallicRoughness": {"baseColorTexture": {
        "index": 0, "texCoord": 0,
        "extensions": {"KHR_texture_transform": transform},
    }}}
    doc, _ = _document(image_uri=_uri(_png(), "image/png"), materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    result = GLTF2().load(prepare_materials(str(source), str(tmp_path / "out.gltf")))
    info = result.materials[0].pbrMetallicRoughness.baseColorTexture
    assert info.extensions["KHR_texture_transform"] == transform


@pytest.mark.parametrize("scale", ["bad", float("nan"), float("inf")])
def test_normal_texture_invalid_scale_is_rejected(tmp_path, scale):
    material = {"normalTexture": {"index": 0, "scale": scale}}
    doc, _ = _document(image_uri=_uri(_png(), "image/png"), materials=[material])
    source = tmp_path / "source.gltf"
    source.write_text(json.dumps(doc))
    with pytest.raises(AnyError) as caught:
        prepare_materials(str(source), str(tmp_path / "out.gltf"))
    assert caught.value.code == 2
    assert "normalTexture scale" in str(caught.value) or "cannot parse glTF" in str(caught.value)

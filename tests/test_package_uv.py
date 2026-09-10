"""UV preparation verified through actual NIF output, not just metadata."""

import copy
import json
import math
import struct

import numpy as np
import pytest

from any2nif.cli import main
from any2nif.package import MANIFEST
from nif2gltf.nif_reader import read_nif
from tests.test_package_materials import _document, _uri, _png


def _source(tmp_path, transform, *, coord=1, normal=False, normalized=False):
    info = {"index": 0, "texCoord": coord,
            "extensions": {"KHR_texture_transform": transform}}
    material = {"pbrMetallicRoughness": {"baseColorTexture": info}}
    if normal:
        material["normalTexture"] = copy.deepcopy(info)
    doc, _ = _document(image_uri=_uri(_png(), "image/png"), materials=[material])
    raw = (struct.pack("<6H", 0, 0, 65535, 0, 0, 65535) if normalized else
           struct.pack("<6f", 0, 0, 1, 0, 0, 1))
    doc["buffers"].append({"uri": _uri(raw), "byteLength": len(raw)})
    doc["bufferViews"].append({"buffer": 1, "byteLength": len(raw)})
    doc["accessors"].append({"bufferView": 2, "componentType": 5123 if normalized else 5126,
                             "normalized": normalized, "count": 3, "type": "VEC2"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_1"] = 2
    source = tmp_path / "uv.gltf"
    source.write_text(json.dumps(doc))
    return source, doc


@pytest.mark.parametrize("transform,expected", [
    ({}, [(0, 0), (1, 0), (0, 1)]),
    ({"offset": [.25, .5], "scale": [2, 3], "rotation": math.pi / 2},
     [(.25, .5), (.25, 2.5), (-2.75, .5)]),
    ({"offset": [0, 1], "scale": [1, -1]}, [(0, 1), (1, 1), (0, 0)]),
    ({"scale": [0, 0]}, [(0, 0)] * 3),
])
@pytest.mark.parametrize("normalized", [False, True])
def test_alternate_uv_and_transform_reach_nif(tmp_path, transform, expected, normalized):
    source, _ = _source(tmp_path, transform, normalized=normalized)
    original = source.read_bytes()
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    mesh = read_nif((output / manifest["mesh"]).read_bytes())[0]
    np.testing.assert_allclose(mesh.uvs, expected, atol=.002)
    assert manifest["textures"] and manifest["collision"] == "convex"
    assert source.read_bytes() == original


def test_override_and_shared_normal_mapping(tmp_path):
    source, doc = _source(tmp_path, {"texCoord": 1, "scale": [2, 2],
                                    "offset": [.25, .5]}, coord=9, normal=True)
    # Two primitives share the original accessor; preparing one must not mutate it.
    doc["meshes"][0]["primitives"].append(copy.deepcopy(doc["meshes"][0]["primitives"][0]))
    source.write_text(json.dumps(doc))
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    meshes = read_nif((output / manifest["mesh"]).read_bytes())
    assert len(meshes) == 2
    for mesh in meshes:
        np.testing.assert_allclose(mesh.uvs, [(.25, .5), (2.25, .5), (.25, 2.5)])
    assert any(path.endswith("_n.dds") for path in manifest["textures"])


@pytest.mark.parametrize("transform,normal", [
    ({"scale": [1, 2]}, True), ({"rotation": .5}, True),
    ({"scale": [-1, -1]}, True), ({"offset": [float("nan"), 0]}, False),
    ({"scale": [1]}, False), ({"rotation": "bad"}, False),
    ({"texCoord": -1}, False), ({"texCoord": True}, False),
    ({"offset": [1e10, 0]}, False),
])
def test_bad_mapping_keeps_existing_package(tmp_path, transform, normal):
    source, doc = _source(tmp_path, {})
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package"]) == 0
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    source, _ = _source(tmp_path, transform, normal=normal)
    assert main([str(source), str(output), "--package"]) == 2
    assert before == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def test_different_texture_mappings_are_rejected(tmp_path, capsys):
    source, doc = _source(tmp_path, {}, normal=True)
    doc["materials"][0]["normalTexture"]["texCoord"] = 0
    source.write_text(json.dumps(doc))
    assert main([str(source), str(tmp_path / "Data"), "--package"]) == 2
    assert "different UV mappings" in capsys.readouterr().err


@pytest.mark.parametrize("change", ["missing", "count", "components", "truncated", "sparse", "nan"])
def test_invalid_selected_accessor_is_rejected(tmp_path, change):
    source, doc = _source(tmp_path, {})
    if change == "missing":
        doc["meshes"][0]["primitives"][0]["attributes"].pop("TEXCOORD_1")
    elif change == "count":
        doc["accessors"][2]["count"] = 2
    elif change == "components":
        doc["accessors"][2]["componentType"] = 5123
    elif change == "truncated":
        doc["bufferViews"][2]["byteLength"] = 4
    elif change == "sparse":
        doc["accessors"][2]["sparse"] = {"count": 1, "indices": {
            "bufferView": 1, "componentType": 5123}, "values": {"bufferView": 2}}
    else:
        doc["buffers"][1]["uri"] = _uri(struct.pack("<6f", float("nan"), 0, 1, 0, 0, 1))
    source.write_text(json.dumps(doc))
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package"]) == 2
    assert not output.exists()

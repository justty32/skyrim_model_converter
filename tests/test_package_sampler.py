"""Sampler metadata contracts through the real package pipeline."""

import base64
import io
import json

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST, texture_references
from any2nif.texture_sampling import CLAMP_TO_EDGE, MIRRORED_REPEAT, REPEAT, sample_rgba
from nif2gltf.nif_reader import read_nif
from tests.test_package_bake import _write_two_uv_gltf


COLORS = np.array([10, 70, 150, 240], dtype=np.uint8)


def _sampler_source(path, *, wrap=REPEAT, nearest=True):
    _write_two_uv_gltf(path)
    doc = json.loads(path.read_text())
    raw = base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1])
    # Triangle centroids are u=1.29 and u=1.11, away from nearest boundaries.
    uv = np.array([[.93, .1], [1.47, .1], [1.47, .4], [.93, .4]], "<f4")
    view = doc["bufferViews"][2]
    start = view.get("byteOffset", 0)
    raw = raw[:start] + uv.tobytes() + raw[start + uv.nbytes:]
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()

    rgba = np.zeros((1, 4, 4), dtype=np.uint8)
    rgba[..., 0] = COLORS
    rgba[..., 3] = 255
    encoded = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(encoded, "PNG")
    doc["images"] = [{"uri": "data:image/png;base64," +
                      base64.b64encode(encoded.getvalue()).decode()}]
    doc["samplers"] = [{"wrapS": wrap, "wrapT": REPEAT,
                        "magFilter": 9728 if nearest else 9729}]
    doc["textures"] = [{"source": 0, "sampler": 0}]
    doc["materials"] = [{"pbrMetallicRoughness": {
        "baseColorTexture": {"index": 0, "texCoord": 0}}}]
    path.write_text(json.dumps(doc))


def _snapshot(path):
    return {item.relative_to(path): item.read_bytes()
            for item in path.rglob("*") if item.is_file()}


def _package_samples(source, output):
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "box"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    mesh = read_nif(nif)[0]
    diffuse = next(ref for ref in texture_references(nif)
                   if ref.endswith("material_0000.dds"))
    with Image.open(output / diffuse.replace("\\", "/")) as image:
        pixels = np.asarray(image.convert("RGBA"))
    uv = np.asarray(mesh.uvs)
    triangles = np.asarray(mesh.triangles)
    centers = np.asarray([uv[triangle].mean(axis=0) for triangle in triangles])
    return mesh, sample_rgba(pixels, centers, linear=False)[:, 0] * 255


@pytest.mark.parametrize(("wrap", "expected"), [
    (CLAMP_TO_EDGE, [240, 240]),
    (MIRRORED_REPEAT, [150, 240]),
    (REPEAT, [70, 10]),
])
def test_nondefault_sampler_is_baked_into_real_dds(tmp_path, wrap, expected):
    source = tmp_path / f"sampler-{wrap}.gltf"
    _sampler_source(source, wrap=wrap, nearest=True)
    mesh, actual = _package_samples(source, tmp_path / f"Data-{wrap}")
    np.testing.assert_allclose(actual, expected, atol=10)
    if wrap != REPEAT:
        assert not np.allclose(actual, [70, 10], atol=10)
    if wrap == REPEAT:
        assert len(mesh.uvs) != 4 or not np.allclose(
            mesh.uvs, [[.93, .1], [1.47, .1], [1.47, .4], [.93, .4]])


@pytest.mark.parametrize("wrap", [CLAMP_TO_EDGE, MIRRORED_REPEAT])
def test_nonrepeat_linear_sampler_alone_triggers_bake(tmp_path, wrap):
    source = tmp_path / f"linear-{wrap}.gltf"
    _sampler_source(source, wrap=wrap, nearest=False)
    mesh, actual = _package_samples(source, tmp_path / f"Data-linear-{wrap}")
    assert len(mesh.uvs) != 4 or not np.allclose(
        mesh.uvs, [[.93, .1], [1.47, .1], [1.47, .4], [.93, .4]])
    if wrap == CLAMP_TO_EDGE:
        expected = [240, 240]
    else:
        # u=1.29 mirrors to .71: x=.71*4-.5=2.34, so blend source
        # columns 2/3 in linear light. u=1.11 mirrors inside the edge texel.
        values = np.array([150, 240]) / 255
        linear = np.where(values <= .04045, values / 12.92,
                          ((values + .055) / 1.055) ** 2.4)
        mixed = linear[0] * .66 + linear[1] * .34
        encoded = 12.92 * mixed if mixed <= .0031308 else 1.055 * mixed ** (1 / 2.4) - .055
        expected = [encoded * 255, 240]
    np.testing.assert_allclose(actual, expected, atol=10)


def test_default_repeat_linear_keeps_original_uv_order(tmp_path):
    source = tmp_path / "default.gltf"
    _sampler_source(source, wrap=REPEAT, nearest=False)
    output = tmp_path / "Data-default"
    mesh, _ = _package_samples(source, output)
    expected = np.array([[.93, .1], [1.47, .1], [1.47, .4], [.93, .4]])
    assert mesh.triangles == [(0, 1, 2), (0, 2, 3)]
    np.testing.assert_allclose(mesh.uvs, expected, atol=.001)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("sampler", 4, "sampler"),
    ("wrapS", 12345, "wrap"),
    ("wrapT", 12345, "wrap"),
    ("magFilter", 12345, "magFilter"),
    ("minFilter", 12345, "minFilter"),
])
def test_invalid_sampler_fails_and_preserves_existing_package(
        tmp_path, capsys, field, value, message):
    source = tmp_path / "source.gltf"
    _sampler_source(source, wrap=REPEAT, nearest=False)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "box"]) == 0
    before = _snapshot(output)
    doc = json.loads(source.read_text())
    if field == "sampler":
        doc["textures"][0]["sampler"] = value
    else:
        doc["samplers"][0][field] = value
    source.write_text(json.dumps(doc))
    assert main([str(source), str(output), "--package", "--bake-size", "64"]) == 2
    assert message.lower() in capsys.readouterr().err.lower()
    assert _snapshot(output) == before

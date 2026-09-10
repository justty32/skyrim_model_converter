"""CLI contracts for generated tangent handedness in direct and atlas paths."""

import base64
import io
import json

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST
from any2nif.texture_sampling import sample_rgba
from nif2gltf.nif_reader import read_nif
from tests.test_package_bake import _write_two_uv_gltf
from tests.test_package_normal_frames import _stored_nif_frames


_POSITIONS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
_TRIANGLES = np.array([[0, 1, 2], [0, 2, 3]])
_BASE_UV = np.array([[.1, .1], [.9, .1], [.9, .9], [.1, .9]])
_NORMAL_RGB = np.array([210, 190, 180])


def _png(color):
    stream = io.BytesIO()
    pixels = np.broadcast_to(np.array(color, np.uint8), (8, 8, 4))
    Image.fromarray(pixels, "RGBA").save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def _source(path, uv, *, node_scale=(1, 1, 1), force_atlas=False):
    _write_two_uv_gltf(path)
    doc = json.loads(path.read_text())
    material = doc["materials"][0]
    infos = [material["emissiveTexture"], material["normalTexture"],
             material["pbrMetallicRoughness"]["baseColorTexture"],
             material["pbrMetallicRoughness"]["metallicRoughnessTexture"]]
    for info in infos:
        info["texCoord"] = 0
        info.pop("extensions", None)
    if force_atlas:
        material["occlusionTexture"]["texCoord"] = 0
        doc["images"][1]["uri"] = _png([255, 255, 255, 255])
    else:
        del material["occlusionTexture"]
    doc["samplers"] = [{}]
    doc["images"][2]["uri"] = _png([*_NORMAL_RGB, 255])
    raw = bytearray(base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1]))
    view = doc["bufferViews"][2]
    offset = view.get("byteOffset", 0)
    raw[offset:offset + uv.astype("<f4").nbytes] = uv.astype("<f4").tobytes()
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    doc["nodes"][0]["scale"] = list(node_scale)
    path.write_text(json.dumps(doc))


def _source_frame(uv, linear):
    tangent_sum = np.zeros((4, 3))
    bitangent_sum = np.zeros((4, 3))
    for triangle in _TRIANGLES:
        a, b, c = triangle
        e1, e2 = _POSITIONS[b] - _POSITIONS[a], _POSITIONS[c] - _POSITIONS[a]
        d1, d2 = uv[b] - uv[a], uv[c] - uv[a]
        determinant = d1[0] * d2[1] - d2[0] * d1[1]
        tangent_sum[triangle] += (d2[1] * e1 - d1[1] * e2) / determinant
        bitangent_sum[triangle] += (-d2[0] * e1 + d1[0] * e2) / determinant
    tangent = tangent_sum[0]
    tangent /= np.linalg.norm(tangent)
    handedness = np.sign(np.dot(np.cross((0, 0, 1), tangent), bitangent_sum[0]))
    tangent = linear @ tangent
    tangent /= np.linalg.norm(tangent)
    normal = np.linalg.inv(linear).T @ np.array((0, 0, 1))
    normal /= np.linalg.norm(normal)
    bitangent = np.cross(normal, tangent) * handedness * np.sign(np.linalg.det(linear))
    return np.column_stack((tangent, bitangent, normal))


def _assert_world_normal(output, expected):
    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    mesh = read_nif(nif)[0]
    frames = _stored_nif_frames(nif)
    normal_ref = next(ref for ref in manifest["textures"] if ref.endswith("_n.dds"))
    with Image.open(output / normal_ref.replace("\\", "/")) as image:
        rgba = np.asarray(image.convert("RGBA"))
    for triangle in np.asarray(mesh.triangles, int):
        uv = np.asarray(mesh.uvs)[triangle].mean(axis=0)
        sample = sample_rgba(rgba, uv[None])[0, :3]
        sample[1] = 1 - sample[1]
        tangent_normal = sample * 2 - 1
        tangent_normal /= np.linalg.norm(tangent_normal)
        frame = frames[triangle].mean(axis=0)
        frame /= np.linalg.norm(frame, axis=0)
        actual = frame @ tangent_normal
        actual /= np.linalg.norm(actual)
        np.testing.assert_allclose(actual, expected, atol=.12)
    return mesh, rgba


@pytest.mark.parametrize(("uv", "node_scale", "cli_args"), [
    pytest.param(_BASE_UV * [-1, 1] + [1, 0], (1, 1, 1), [], id="mirror-u"),
    pytest.param(_BASE_UV * [1, -1] + [0, 1], (1, 1, 1), [], id="mirror-v"),
    pytest.param(_BASE_UV, (-1, 1, 1), [], id="node-reflection"),
    pytest.param(_BASE_UV, (1, 1, 1), ["--scale", "-2"], id="global-reflection"),
])
def test_direct_generated_tangent_preserves_uv_handedness(tmp_path, uv, node_scale, cli_args):
    source = tmp_path / "source.gltf"
    _source(source, uv, node_scale=node_scale)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "none", *cli_args]) == 0
    linear = np.diag(node_scale)
    if cli_args:
        linear = -2 * np.eye(3) @ linear
    encoded = _NORMAL_RGB / 255 * 2 - 1
    encoded /= np.linalg.norm(encoded)
    expected = _source_frame(uv, linear) @ encoded
    expected /= np.linalg.norm(expected)
    mesh, normal_map = _assert_world_normal(output, expected)
    np.testing.assert_allclose(mesh.uvs, uv, atol=.001)
    assert normal_map.shape[:2] == (8, 8)  # No atlas or unnecessary resizing.


@pytest.mark.parametrize(("uv", "global_scale"), [
    pytest.param(_BASE_UV, -1, id="regular-negative-global"),
    pytest.param(_BASE_UV * [-1, 1] + [1, 0], 1, id="mirror-u-positive-global"),
    pytest.param(_BASE_UV * [-1, 1] + [1, 0], -1, id="mirror-u-negative-global"),
])
def test_forced_atlas_with_global_reflection_preserves_generated_basis(
        tmp_path, uv, global_scale):
    source = tmp_path / "atlas.gltf"
    _source(source, uv, force_atlas=True)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "none", "--scale", str(global_scale)]) == 0
    encoded = _NORMAL_RGB / 255 * 2 - 1
    encoded /= np.linalg.norm(encoded)
    expected = _source_frame(uv, global_scale * np.eye(3)) @ encoded
    expected /= np.linalg.norm(expected)
    _assert_world_normal(output, expected)

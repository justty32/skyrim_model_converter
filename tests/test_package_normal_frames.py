"""CLI contract for supplied glTF tangents during cross-UV normal baking."""

import base64
import io
import json
import math
import struct

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST
from any2nif.texture_sampling import CLAMP_TO_EDGE, sample_rgba
from nif2gltf.nif_reader import read_nif
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header
from tests.test_package_bake import _write_two_uv_gltf


def _add_tangents_and_transform(path, transform, *, shared_mapping=False,
                                supplied_tangent=True):
    doc = json.loads(path.read_text())
    raw = base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1])
    tangents = b""
    if supplied_tangent:
        tangents = np.tile(np.array([0, -1, 0, 1], dtype="<f4"), (4, 1)).tobytes()
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(raw),
                                   "byteLength": len(tangents)})
        doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1,
                                 "componentType": 5126, "count": 4, "type": "VEC4"})
        doc["meshes"][0]["primitives"][0]["attributes"]["TANGENT"] = len(doc["accessors"]) - 1
    doc["materials"][0]["normalTexture"]["extensions"] = {"KHR_texture_transform": transform}
    normal = np.broadcast_to(np.array([210, 190, 180, 255], dtype=np.uint8), (8, 8, 4))
    encoded_normal = io.BytesIO()
    Image.fromarray(normal, "RGBA").save(encoded_normal, "PNG")
    doc["images"][2]["uri"] = ("data:image/png;base64," +
                                base64.b64encode(encoded_normal.getvalue()).decode())
    if shared_mapping:
        material = doc["materials"][0]
        infos = [material["emissiveTexture"], material["occlusionTexture"],
                 material["normalTexture"],
                 material["pbrMetallicRoughness"]["baseColorTexture"],
                 material["pbrMetallicRoughness"]["metallicRoughnessTexture"]]
        for info in infos:
            info["texCoord"] = 1
            info["extensions"] = {"KHR_texture_transform": dict(transform)}
        # Do not let the pre-existing unconditional AO rule trigger this bake:
        # shared cases specifically exercise normal-transform routing.
        del material["occlusionTexture"]
    payload = raw + tangents
    doc["buffers"][0] = {"uri": "data:application/octet-stream;base64," +
                          base64.b64encode(payload).decode(), "byteLength": len(payload)}
    path.write_text(json.dumps(doc))


def _reference_generated_source_frame(sampling):
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    normal = np.array([0, 0, 1], dtype=float)
    uv0 = np.array([[.1, .1], [.9, .1], [.9, .9], [.1, .9]])
    uv1 = np.stack((1 - uv0[:, 1], uv0[:, 0]), axis=1) @ sampling
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    tangent_sum = np.zeros((4, 3))
    bitangent_sum = np.zeros((4, 3))
    for triangle in triangles:
        a, b, c = triangle
        edge1, edge2 = positions[b] - positions[a], positions[c] - positions[a]
        duv1, duv2 = uv1[b] - uv1[a], uv1[c] - uv1[a]
        determinant = duv1[0] * duv2[1] - duv2[0] * duv1[1]
        tangent = (duv2[1] * edge1 - duv1[1] * edge2) / determinant
        bitangent = (-duv2[0] * edge1 + duv1[0] * edge2) / determinant
        tangent_sum[triangle] += tangent
        bitangent_sum[triangle] += bitangent
    tangent_sum -= normal * (tangent_sum @ normal)[:, None]
    tangent_sum /= np.linalg.norm(tangent_sum, axis=1)[:, None]
    cross_nt = np.cross(normal, tangent_sum)
    signs = np.sign(np.sum(cross_nt * bitangent_sum, axis=1))
    bitangent = cross_nt * signs[:, None]
    return np.column_stack((tangent_sum[0], bitangent[0], normal))


def _identity_shared_authored_tangent(path, handedness, node_angle, node_scale=(1, 1, 1),
                                      *, normal_present=True):
    doc = json.loads(path.read_text())
    material = doc["materials"][0]
    del material["occlusionTexture"]
    infos = [material["emissiveTexture"], material["normalTexture"],
             material["pbrMetallicRoughness"]["baseColorTexture"],
             material["pbrMetallicRoughness"]["metallicRoughnessTexture"]]
    for info in infos:
        info["texCoord"] = 0
        info.pop("extensions", None)
    doc["samplers"] = [{}]
    h = 2 ** -0.5
    tangent = np.tile(np.array([h, h, 0, handedness], dtype="<f4"), (4, 1)).tobytes()
    raw = base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1])
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(raw),
                               "byteLength": len(tangent)})
    doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1,
                             "componentType": 5126, "count": 4, "type": "VEC4"})
    doc["meshes"][0]["primitives"][0]["attributes"]["TANGENT"] = len(doc["accessors"]) - 1
    if not normal_present:
        doc["meshes"][0]["primitives"][0]["attributes"].pop("NORMAL")
    payload = raw + tangent
    doc["buffers"][0] = {"uri": "data:application/octet-stream;base64," +
                          base64.b64encode(payload).decode(), "byteLength": len(payload)}
    normal = np.broadcast_to(np.array([210, 190, 180, 255], dtype=np.uint8), (8, 8, 4))
    stream = io.BytesIO()
    Image.fromarray(normal, "RGBA").save(stream, "PNG")
    doc["images"][2]["uri"] = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    doc["nodes"][0]["rotation"] = [0, 0, math.sin(node_angle / 2), math.cos(node_angle / 2)]
    doc["nodes"][0]["scale"] = list(node_scale)
    path.write_text(json.dumps(doc))


def _stored_nif_frames(data):
    header = _read_header(_Reader(data))
    block = header["types"].index("BSTriShape")
    start = header["offsets"][block]
    descriptor = struct.unpack_from("<Q", data, start + 100)[0]
    stride = (descriptor & 0xF) * 4
    vertex_count = struct.unpack_from("<H", data, start + 110)[0]
    vertex_data = start + 116

    def snorm(value):
        return value / 127.5 - 1.0

    frames = []
    for index in range(vertex_count):
        offset = vertex_data + index * stride
        bx = struct.unpack_from("<f", data, offset + 12)[0]
        nx, ny, nz, by, tx, ty, tz, bz = data[offset + 20:offset + 28]
        skyrim = np.column_stack(((snorm(tx), snorm(ty), snorm(tz)),
                                  (bx, snorm(by), snorm(bz)),
                                  (snorm(nx), snorm(ny), snorm(nz))))
        # NIF reader exposes glTF axes: Skyrim (x,y,z) -> glTF (x,z,-y).
        frames.append(skyrim[[0, 2, 1], :] * np.array([[1], [1], [-1]]))
    return np.asarray(frames)


@pytest.mark.parametrize("transform", [
    {"rotation": math.pi / 2},
    {"scale": [-1, 1], "offset": [1, 0]},
    {"scale": [-1, -1], "offset": [1, 1]},
    {"scale": [2, .5]},
    {"rotation": .37, "scale": [2, .5]},
])
@pytest.mark.parametrize(("shared_mapping", "supplied_tangent"), [
    pytest.param(False, True, id="cross-slot-supplied"),
    pytest.param(True, True, id="shared-supplied"),
    pytest.param(True, False, id="shared-generated"),
])
def test_cli_preserves_world_normal_for_normal_texture_transform(
        tmp_path, transform, shared_mapping, supplied_tangent):
    source = tmp_path / "source.gltf"
    _write_two_uv_gltf(source)
    _add_tangents_and_transform(source, transform, shared_mapping=shared_mapping,
                                supplied_tangent=supplied_tangent)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "none"]) == 0

    manifest = json.loads((output / MANIFEST).read_text())
    nif_data = (output / manifest["mesh"]).read_bytes()
    mesh = read_nif(nif_data)[0]
    normal_ref = next(ref for ref in manifest["textures"] if ref.endswith("_n.dds"))
    with Image.open(output / normal_ref.replace("\\", "/")) as image:
        normal_map = np.asarray(image.convert("RGBA"))

    uvs = np.asarray(mesh.uvs, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=int)
    target = _stored_nif_frames(nif_data)

    angle = transform.get("rotation", 0.0)
    scale = transform.get("scale", [1, 1])
    c, s = math.cos(angle), math.sin(angle)
    sampling = np.diag(scale) @ np.array([[c, s], [-s, c]])
    if supplied_tangent:
        # Source TEXCOORD_1 has supplied T=-Y, w=+1 => B=+X, N=+Z.
        source_tb = np.array([[0, 1], [-1, 0], [0, 0]], dtype=float) @ np.linalg.inv(sampling).T
        source_tb /= np.linalg.norm(source_tb, axis=0)
        source_frame = np.column_stack((source_tb, [0, 0, 1]))
    else:
        source_frame = _reference_generated_source_frame(sampling)
    encoded = np.array([210, 190, 180], dtype=float) / 255 * 2 - 1
    encoded /= np.linalg.norm(encoded)
    source_world = source_frame @ encoded
    # read_nif exposes its Mesh IR in glTF axes, so compare in source glTF space.
    expected_world = source_world.copy()
    expected_world /= np.linalg.norm(expected_world)

    for face, triangle in enumerate(triangles):
        uv = uvs[triangle].mean(axis=0)
        sampled = sample_rgba(normal_map, uv[None], wrap_s=CLAMP_TO_EDGE,
                              wrap_t=CLAMP_TO_EDGE)[0, :3]
        sampled[1] = 1 - sampled[1]  # undo tex2dds' Skyrim green-channel flip
        tangent_normal = sampled * 2 - 1
        tangent_normal /= np.linalg.norm(tangent_normal)
        frame = target[triangle].mean(axis=0)
        frame /= np.linalg.norm(frame, axis=0)
        actual_world = frame @ tangent_normal
        actual_world /= np.linalg.norm(actual_world)
        np.testing.assert_allclose(actual_world, expected_world, atol=.12,
                                   err_msg=f"triangle {face}, transform={transform}")


@pytest.mark.parametrize(("handedness", "node_angle", "node_scale", "cli_args"), [
    pytest.param(1, 0, (1, 1, 1), [], id="identity-w+"),
    pytest.param(-1, 0, (1, 1, 1), [], id="identity-w-"),
    pytest.param(1, math.pi / 3, (1, 1, 1), [], id="node-rotation"),
    pytest.param(1, 0, (-1, 1, 1), [], id="node-reflection"),
    pytest.param(1, 0, (2, .5, 1), [], id="node-nonuniform"),
    pytest.param(1, 0, (1, 1, 1), ["--scale", "-2", "--up-axis", "z"],
                 id="global-zup-reflection"),
])
def test_identity_shared_uv_preserves_authored_tangent_basis(
        tmp_path, handedness, node_angle, node_scale, cli_args):
    source = tmp_path / "authored.gltf"
    _write_two_uv_gltf(source)
    _identity_shared_authored_tangent(source, handedness, node_angle, node_scale)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "none", *cli_args]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    nif_data = (output / manifest["mesh"]).read_bytes()
    mesh = read_nif(nif_data)[0]
    normal_ref = next(ref for ref in manifest["textures"] if ref.endswith("_n.dds"))
    with Image.open(output / normal_ref.replace("\\", "/")) as image:
        assert image.size == (8, 8)  # direct path preserves source resolution; no atlas
        normal_map = np.asarray(image.convert("RGBA"))
    uvs, triangles = np.asarray(mesh.uvs, float), np.asarray(mesh.triangles, int)
    np.testing.assert_allclose(uvs, [[.1, .1], [.9, .1], [.9, .9], [.1, .9]], atol=.001)
    target = _stored_nif_frames(nif_data)

    h = 2 ** -0.5
    c, s = math.cos(node_angle), math.sin(node_angle)
    linear = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]) @ np.diag(node_scale)
    if cli_args:
        zup_to_yup = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
        linear = (-2 * np.eye(3)) @ zup_to_yup @ linear
    tangent = linear @ np.array((h, h, 0))
    tangent /= np.linalg.norm(tangent)
    normal = np.linalg.inv(linear).T @ np.array((0, 0, 1))
    normal /= np.linalg.norm(normal)
    bitangent = np.cross(normal, tangent) * handedness * np.sign(np.linalg.det(linear))
    authored = np.column_stack((tangent, bitangent, normal))
    encoded = np.array([210, 190, 180], float) / 255 * 2 - 1
    encoded /= np.linalg.norm(encoded)
    expected = authored @ encoded
    expected /= np.linalg.norm(expected)
    for triangle in triangles:
        uv = uvs[triangle].mean(axis=0)
        sampled = sample_rgba(normal_map, uv[None])[0, :3]
        sampled[1] = 1 - sampled[1]
        tangent_normal = sampled * 2 - 1
        tangent_normal /= np.linalg.norm(tangent_normal)
        frame = target[triangle].mean(axis=0)
        frame /= np.linalg.norm(frame, axis=0)
        actual = frame @ tangent_normal
        actual /= np.linalg.norm(actual)
        np.testing.assert_allclose(actual, expected, atol=.12)


def test_supplied_tangent_is_ignored_when_normal_attribute_is_missing(tmp_path):
    source = tmp_path / "generated-normal.gltf"
    _write_two_uv_gltf(source)
    _identity_shared_authored_tangent(source, 1, 0, normal_present=False)
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "none"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    frames = _stored_nif_frames((output / manifest["mesh"]).read_bytes())
    # UV-derived basis is +X/+Y; the authored 45-degree tangent must be ignored.
    np.testing.assert_allclose(frames[:, :, 0], np.tile((1, 0, 0), (len(frames), 1)), atol=.02)
    np.testing.assert_allclose(frames[:, :, 1], np.tile((0, 1, 0), (len(frames), 1)), atol=.02)

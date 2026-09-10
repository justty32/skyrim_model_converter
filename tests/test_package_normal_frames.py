"""CLI contract for supplied glTF tangents during cross-UV normal baking."""

import base64
import io
import json
import math

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST
from any2nif.texture_sampling import CLAMP_TO_EDGE, sample_rgba
from nif2gltf.nif_reader import read_nif
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


def _reference_vertex_frames(positions, normals, uvs, triangles):
    tangent_sum = np.zeros_like(positions, dtype=float)
    for tri in triangles:
        a, b, c = tri
        e1, e2 = positions[b] - positions[a], positions[c] - positions[a]
        d1, d2 = uvs[b] - uvs[a], uvs[c] - uvs[a]
        tangent = (d2[1] * e1 - d1[1] * e2) / (d1[0] * d2[1] - d2[0] * d1[1])
        tangent_sum[tri] += tangent
    tangent_sum -= normals * np.sum(normals * tangent_sum, axis=1)[:, None]
    tangent_sum /= np.linalg.norm(tangent_sum, axis=1)[:, None]
    bitangent = np.cross(normals, tangent_sum)
    return np.stack((tangent_sum, bitangent, normals), axis=2)


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
    mesh = read_nif((output / manifest["mesh"]).read_bytes())[0]
    normal_ref = next(ref for ref in manifest["textures"] if ref.endswith("_n.dds"))
    with Image.open(output / normal_ref.replace("\\", "/")) as image:
        normal_map = np.asarray(image.convert("RGBA"))

    positions = np.asarray(mesh.positions, dtype=float)
    normals = np.asarray(mesh.normals, dtype=float)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    uvs = np.asarray(mesh.uvs, dtype=float)
    triangles = np.asarray(mesh.triangles, dtype=int)
    target = _reference_vertex_frames(positions, normals, uvs, triangles)

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

"""End-to-end contracts for baking material UVs to per-primitive atlases."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import struct

import numpy as np
import pytest
from PIL import Image
from pygltflib import GLTF2

from any2nif.cli import main
from any2nif.errors import AnyError
from any2nif.package import MANIFEST, texture_references
from any2nif.package_materials import _buffers, prepare_materials
from any2nif.texture_sampling import CLAMP_TO_EDGE, sample_rgba
from gltf2nif.gltf_reader import _read_accessor
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif


def _png(pixels):
    output = io.BytesIO()
    Image.fromarray(np.asarray(pixels, dtype=np.uint8), "RGBA").save(output, "PNG")
    return output.getvalue()


def _uri(raw, mime):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _gradient(kind):
    y, x = np.mgrid[:8, :8]
    if kind == "base":
        result = np.stack((32 + x * 20, 24 + y * 22, 40 + x * 5, 80 + y * 12), axis=-1)
        return result.astype(np.uint8)
    if kind == "ao":
        value = 48 + x * 18
        return np.stack((value, value, value, np.full_like(value, 255)), axis=-1).astype(np.uint8)
    if kind == "normal":
        return np.broadcast_to(np.array([224, 128, 192, 255]), (8, 8, 4)).copy()
    return np.stack((20 + y * 18, 40 + x * 14, 30 + (x + y) * 8,
                     np.full_like(x, 255)), axis=-1).astype(np.uint8)


def _write_two_uv_gltf(path, *, two_primitives=False, missing_uv1=False):
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], "<f4")
    normals = np.tile(np.array([[0, 0, 1]], "<f4"), (4, 1))
    uv0 = np.array([[.1, .1], [.9, .1], [.9, .9], [.1, .9]], "<f4")
    # Rotated mapping, deliberately distinct from TEXCOORD_0.
    uv1 = np.stack((1 - uv0[:, 1], uv0[:, 0]), axis=1).astype("<f4")
    indices = np.array([0, 1, 2, 0, 2, 3], "<u4")
    chunks = [positions.tobytes(), normals.tobytes(), uv0.tobytes()]
    if not missing_uv1:
        chunks.append(uv1.tobytes())
    chunks.append(indices.tobytes())
    offsets, cursor = [], 0
    for chunk in chunks:
        offsets.append(cursor)
        cursor += len(chunk)
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3",
         "min": [0, 0, 0], "max": [1, 1, 0]},
        {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC3"},
        {"bufferView": 2, "componentType": 5126, "count": 4, "type": "VEC2"},
    ]
    if not missing_uv1:
        accessors.append({"bufferView": 3, "componentType": 5126, "count": 4, "type": "VEC2"})
    index_view = len(chunks) - 1
    accessors.append({"bufferView": index_view, "componentType": 5125,
                      "count": 6, "type": "SCALAR"})
    attrs = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}
    if not missing_uv1:
        attrs["TEXCOORD_1"] = 3
    primitive = {"attributes": attrs, "indices": len(accessors) - 1, "material": 0}
    primitives = [primitive, json.loads(json.dumps(primitive))] if two_primitives else [primitive]
    images = [_gradient(kind) for kind in ("base", "ao", "normal", "extra")]
    doc = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}],
        "buffers": [{"uri": _uri(b"".join(chunks), "application/octet-stream"),
                     "byteLength": cursor}],
        "bufferViews": [{"buffer": 0, "byteOffset": off, "byteLength": len(raw)}
                        for off, raw in zip(offsets, chunks)],
        "accessors": accessors,
        "images": [{"uri": _uri(_png(image), "image/png")} for image in images],
        "samplers": [{"wrapS": CLAMP_TO_EDGE, "wrapT": CLAMP_TO_EDGE,
                      "magFilter": 9729}],
        "textures": [{"source": i, "sampler": 0} for i in range(4)],
        "materials": [{
            "emissiveTexture": {"index": 3, "texCoord": 0},
            "occlusionTexture": {"index": 1, "texCoord": 1, "strength": .75},
            "normalTexture": {"index": 2, "texCoord": 1},
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0, "texCoord": 0},
                "baseColorFactor": [.5, .75, .25, .3],
                # Deliberately shares the emissive source texture.
                "metallicRoughnessTexture": {"index": 3, "texCoord": 1},
            },
        }],
    }
    path.write_text(json.dumps(doc))
    return images


def _load_texture(gltf, texture_index):
    uri = gltf.images[gltf.textures[texture_index].source].uri
    raw = base64.b64decode(uri.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        return np.asarray(image.convert("RGBA"))


def _prepared_geometry(path):
    gltf = GLTF2().load(str(path))
    buffers = _buffers(gltf, str(path.parent))
    primitive = gltf.meshes[0].primitives[0]
    positions = np.asarray(_read_accessor(gltf, buffers, primitive.attributes.POSITION))
    uv = np.asarray(_read_accessor(gltf, buffers, primitive.attributes.TEXCOORD_0))
    triangles = np.asarray(_read_accessor(gltf, buffers, primitive.indices)).reshape(-1, 3)
    return gltf, positions, uv, triangles


def _centroid_samples(gltf, positions, atlas_uv, triangles, info):
    image = _load_texture(gltf, info.index)
    samples, source_centers = [], []
    for triangle in triangles:
        target_uv = atlas_uv[triangle].mean(axis=0)
        samples.append(sample_rgba(image, target_uv[None], wrap_s=CLAMP_TO_EDGE,
                                   wrap_t=CLAMP_TO_EDGE)[0])
        source_centers.append(positions[triangle, :2].mean(axis=0) * .8 + .1)
    return np.asarray(samples), np.asarray(source_centers)


def test_bakes_cross_uv_slots_at_triangle_centroids(tmp_path):
    source = tmp_path / "source.gltf"
    originals = _write_two_uv_gltf(source)
    before = hashlib.sha256(source.read_bytes()).digest()
    output = tmp_path / "prepared.gltf"
    prepare_materials(str(source), str(output), bake_size=64)
    assert hashlib.sha256(source.read_bytes()).digest() == before

    gltf, positions, atlas_uv, triangles = _prepared_geometry(output)
    material = gltf.materials[0]
    assert material.occlusionTexture is None
    assert {material.emissiveTexture.texCoord, material.normalTexture.texCoord,
            material.pbrMetallicRoughness.baseColorTexture.texCoord,
            material.pbrMetallicRoughness.metallicRoughnessTexture.texCoord} == {0}
    assert material.pbrMetallicRoughness.baseColorFactor == [.5, .75, .25, .3]

    base, source_uv0 = _centroid_samples(
        gltf, positions, atlas_uv, triangles,
        material.pbrMetallicRoughness.baseColorTexture)
    source_uv1 = np.stack((1 - source_uv0[:, 1], source_uv0[:, 0]), axis=1)
    base_linear = sample_rgba(originals[0], source_uv0, wrap_s=CLAMP_TO_EDGE,
                              wrap_t=CLAMP_TO_EDGE, srgb=True)
    ao = sample_rgba(originals[1], source_uv1, wrap_s=CLAMP_TO_EDGE,
                     wrap_t=CLAMP_TO_EDGE)[:, 0]
    expected_rgb = base_linear[:, :3] * (.25 + .75 * ao[:, None]) * [.5, .75, .25]
    # Decode the prepared PNG before comparing its quantized sRGB values.
    actual_linear = np.where(base[:, :3] <= .04045, base[:, :3] / 12.92,
                             ((base[:, :3] + .055) / 1.055) ** 2.4)
    np.testing.assert_allclose(actual_linear, expected_rgb, atol=.006)
    # baseColorFactor alpha remains metadata and is not multiplied into texture alpha.
    np.testing.assert_allclose(base[:, 3], base_linear[:, 3], atol=2 / 255)

    emissive, _ = _centroid_samples(gltf, positions, atlas_uv, triangles,
                                    material.emissiveTexture)
    expected_emissive = sample_rgba(originals[3], source_uv0, wrap_s=CLAMP_TO_EDGE,
                                    wrap_t=CLAMP_TO_EDGE)
    np.testing.assert_allclose(emissive, expected_emissive, atol=.04)
    mr, _ = _centroid_samples(gltf, positions, atlas_uv, triangles,
                              material.pbrMetallicRoughness.metallicRoughnessTexture)
    expected_mr = sample_rgba(originals[3], source_uv1, wrap_s=CLAMP_TO_EDGE,
                              wrap_t=CLAMP_TO_EDGE)
    np.testing.assert_allclose(mr, expected_mr, atol=.04)


def test_rotated_normal_uv_preserves_source_tangent_space_direction(tmp_path):
    source = tmp_path / "normal.gltf"
    _write_two_uv_gltf(source)
    output = tmp_path / "prepared.gltf"
    prepare_materials(str(source), str(output), bake_size=64)
    gltf, positions, atlas_uv, triangles = _prepared_geometry(output)
    normal = _load_texture(gltf, gltf.materials[0].normalTexture.index)

    # Source TEXCOORD_1 rotates the surface tangent from +X to -Y.  Reorientation
    # must encode the same world-space direction in the generated atlas basis.
    for triangle in triangles:
        p = positions[triangle]
        t = atlas_uv[triangle]
        edge1, edge2 = p[1] - p[0], p[2] - p[0]
        duv1, duv2 = t[1] - t[0], t[2] - t[0]
        determinant = duv1[0] * duv2[1] - duv1[1] * duv2[0]
        tangent = (edge1 * duv2[1] - edge2 * duv1[1]) / determinant
        tangent /= np.linalg.norm(tangent)
        normal_xyz = sample_rgba(normal, t.mean(axis=0)[None],
                                 wrap_s=CLAMP_TO_EDGE, wrap_t=CLAMP_TO_EDGE)[0, :3] * 2 - 1
        normal_xyz /= np.linalg.norm(normal_xyz)
        bitangent = np.cross([0, 0, 1], tangent)
        world = tangent * normal_xyz[0] + bitangent * normal_xyz[1] + np.array([0, 0, 1]) * normal_xyz[2]
        encoded = np.array([224, 128, 192]) / 255 * 2 - 1
        expected = np.array([encoded[1], -encoded[0], encoded[2]])
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(world / np.linalg.norm(world), expected, atol=.08)


def test_shared_material_gets_independent_atlas_images_per_primitive(tmp_path):
    source = tmp_path / "shared.gltf"
    _write_two_uv_gltf(source, two_primitives=True)
    result = GLTF2().load(prepare_materials(str(source), str(tmp_path / "out.gltf"), bake_size=64))
    first, second = result.meshes[0].primitives
    assert first.material != second.material
    first_info = result.materials[first.material].pbrMetallicRoughness.baseColorTexture
    second_info = result.materials[second.material].pbrMetallicRoughness.baseColorTexture
    assert first_info.index != second_info.index
    assert result.textures[first_info.index].source != result.textures[second_info.index].source


def test_package_bake_size_produces_dds_nif_and_collision(tmp_path):
    source = tmp_path / "asset.gltf"
    _write_two_uv_gltf(source)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "box"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    assert read_nif(nif)[0].triangles
    # The writer represents the requested box as its eight-vertex convex shape.
    assert "bhkConvexVerticesShape" in _read_header(_Reader(nif))["types"]
    assert manifest["collision"] == "box"
    refs = texture_references(nif)
    assert refs == manifest["textures"] and refs
    for ref in refs:
        with Image.open(output / ref.replace("\\", "/")) as image:
            image.load()
            assert image.format == "DDS"


def test_missing_cross_uv_preserves_source_and_existing_package(tmp_path):
    good = tmp_path / "good.gltf"
    _write_two_uv_gltf(good)
    output = tmp_path / "data"
    assert main([str(good), str(output), "--package", "--bake-size", "64",
                 "--collision", "box"]) == 0
    package_before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    bad = tmp_path / "bad.gltf"
    _write_two_uv_gltf(bad, missing_uv1=True)
    source_before = bad.read_bytes()
    assert main([str(bad), str(output), "--package", "--bake-size", "64"]) == 2
    assert bad.read_bytes() == source_before
    assert {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()} == package_before


def test_bake_size_requires_package():
    with pytest.raises(SystemExit):
        main(["missing.gltf", "out.nif", "--bake-size", "63"])
    assert main(["missing.gltf", "out.nif", "--bake-size", "64"]) == 1


@pytest.mark.parametrize('bake_size', [64, 1024])
def test_subpixel_island_keeps_its_own_color(tmp_path, bake_size):
    source = tmp_path / 'tiny-island.gltf'
    _write_two_uv_gltf(source)
    doc = json.loads(source.read_text())
    positions = np.array([[0,0,0], [1,0,0], [1,1,0], [0,1,0],
                          [2,0,0], [2.00001,0,0], [2,.00001,0]], dtype='<f4')
    arrays = [positions, np.tile([0,0,1], (7,1)).astype('<f4'),
              np.array([[.25,.5]] * 4 + [[.75,.5]] * 3, dtype='<f4'),
              np.tile([.5,.5], (7,1)).astype('<f4'),
              np.array([0,1,2,0,2,3,4,5,6], dtype='<u4')]
    chunks = [a.tobytes() for a in arrays]
    offset = 0
    doc['bufferViews'] = []
    for raw in chunks:
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(raw)})
        offset += len(raw)
    doc['buffers'] = [{'uri': _uri(b''.join(chunks), 'application/octet-stream'), 'byteLength': offset}]
    for i, accessor in enumerate(doc['accessors']):
        accessor['count'] = 9 if i == 4 else 7
    doc['accessors'][0]['max'] = [2.00001,1,0]
    doc['images'][0]['uri'] = _uri(_png([[[230,10,20,255],[5,25,240,255]]]), 'image/png')
    material = doc['materials'][0]
    material.pop('normalTexture')
    material.pop('emissiveTexture')
    material['occlusionTexture']['strength'] = 0
    material['pbrMetallicRoughness'].pop('metallicRoughnessTexture')
    material['pbrMetallicRoughness']['baseColorFactor'] = [1,1,1,1]
    source.write_text(json.dumps(doc))
    target = tmp_path / 'Data'
    assert main([str(source), str(target), '--package', '--bake-size', str(bake_size),
                 '--collision', 'none']) == 0
    manifest = json.loads((target / MANIFEST).read_text())
    mesh = read_nif((target / manifest['mesh']).read_bytes())[0]
    assert len(mesh.triangles) == 3
    uv = np.asarray(mesh.uvs)
    with Image.open(target / manifest['textures'][0].replace('\\', '/')) as image:
        rgba = np.asarray(image.convert('RGBA'))
    large = sample_rgba(rgba, uv[list(mesh.triangles[0])].mean(axis=0)[None])[0]
    tiny = sample_rgba(rgba, uv[list(mesh.triangles[-1])].mean(axis=0)[None])[0]
    assert large[0] > .8 and large[2] < .15
    assert tiny[2] > .8 and tiny[0] < .15

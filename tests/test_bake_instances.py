"""Baking must preserve transformed instances, including reflections."""

import json

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST
from any2nif.package_materials import prepare_materials
from gltf2nif import read_gltf
from nif2gltf.nif_reader import read_nif
from tests.test_package_bake import _write_two_uv_gltf, _prepared_geometry, _load_texture


def test_instances_keep_world_geometry_and_independent_materials(tmp_path):
    source = tmp_path / 'instances.gltf'
    _write_two_uv_gltf(source)
    doc = json.loads(source.read_text())
    doc['nodes'] = [{'mesh': 0, 'scale': [2, 3, 1]},
                    {'mesh': 0, 'scale': [-2, 1, 1], 'translation': [5, 0, 0]}]
    doc['scenes'][0]['nodes'] = [0, 1]
    source.write_text(json.dumps(doc))
    original = read_gltf(str(source))
    target = tmp_path / 'prepared.gltf'
    prepare_materials(str(source), str(target), bake_size=64)
    output = read_gltf(str(target))
    assert len(output) == 2
    assert output[0].material != output[1].material
    for before, after in zip(original, output):
        np.testing.assert_allclose(np.asarray(after.positions)[after.triangles],
                                   np.asarray(before.positions)[before.triangles])
        np.testing.assert_allclose(np.asarray(after.normals)[after.triangles],
                                   np.asarray(before.normals)[before.triangles])


def test_negative_scale_keeps_baked_normal_pixels_and_reflects_geometry(tmp_path):
    source = tmp_path / 'reflected.gltf'
    _write_two_uv_gltf(source)
    pixels = []
    meshes = []
    for scale in (1, -1):
        target = tmp_path / f'Data-{scale}'
        assert main([str(source), str(target), '--package', '--bake-size', '64',
                     '--scale', str(scale), '--collision', 'box']) == 0
        manifest = json.loads((target / MANIFEST).read_text())
        meshes.append(read_nif((target / manifest['mesh']).read_bytes())[0])
        normal_path = next(p for p in manifest['textures'] if p.endswith('_n.dds'))
        with Image.open(target / normal_path.replace('\\', '/')) as image:
            pixels.append(np.asarray(image.convert('RGB'), dtype=float))
    # The generated NIF bitangent now carries reflection handedness itself.
    # Flipping image Y as well would compensate twice and reverse the light.
    np.testing.assert_array_equal(pixels[1], pixels[0])
    np.testing.assert_allclose(meshes[1].positions, -np.asarray(meshes[0].positions))
    assert meshes[1].triangles == [(a,c,b) for a,b,c in meshes[0].triangles]


@pytest.mark.parametrize('change', ['animation', 'morph', 'sampler', 'ao_strength', 'sparse'])
def test_invalid_bake_sources_do_not_publish(tmp_path, change):
    source = tmp_path / 'invalid.gltf'
    _write_two_uv_gltf(source)
    doc = json.loads(source.read_text())
    if change == 'animation':
        doc['animations'] = [{'channels': [], 'samplers': []}]
    elif change == 'morph':
        doc['meshes'][0]['primitives'][0]['targets'] = [{'POSITION': 0}]
    elif change == 'sampler':
        doc['samplers'][0]['wrapS'] = 999
    elif change == 'ao_strength':
        doc['materials'][0]['occlusionTexture']['strength'] = float('nan')
    else:
        doc['accessors'][3]['sparse'] = {'count': 1, 'indices': {'bufferView': 4,
            'componentType': 5125}, 'values': {'bufferView': 3}}
    source.write_text(json.dumps(doc))
    target = tmp_path / 'Data'
    assert main([str(source), str(target), '--package', '--bake-size', '64']) == (
        3 if change in ('animation','morph') else 2)
    assert not target.exists()


@pytest.mark.parametrize('extra', [
    {'attributes': {'POSITION': 0}, 'indices': 4, 'mode': 1},
    {'attributes': {}, 'mode': 4},
])
def test_baking_preserves_reader_skip_rules(tmp_path, extra):
    source = tmp_path / 'mixed.gltf'
    _write_two_uv_gltf(source)
    doc = json.loads(source.read_text())
    doc['nodes'][0]['scale'] = [-1, 1, 1]
    doc['meshes'][0]['primitives'].append(extra)
    source.write_text(json.dumps(doc))
    target = tmp_path / 'Data'
    assert main([str(source), str(target), '--package', '--bake-size', '64']) == 0
    manifest = json.loads((target / MANIFEST).read_text())
    assert len(read_nif((target / manifest['mesh']).read_bytes())) == 1

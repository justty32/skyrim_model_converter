"""Complete strip/fan geometry through the reader and production package CLI."""
import base64
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from any2nif.package import MANIFEST
from gltf2nif import read_gltf
from gltf2nif._binwriter import GltfError
from gltf2nif.geometry import SKYRIM_UNITS_PER_METRE
from nif2gltf.nif_reader import read_nif

CASES = [
    pytest.param(5, None, [(0,1,2),(2,1,3),(2,3,4),(4,3,5)], id='strip-unindexed'),
    pytest.param(5, [0,1,2,3,4,5], [(0,1,2),(2,1,3),(2,3,4),(4,3,5)], id='strip-indexed'),
    # Collapsed windows still consume parity: final face has odd winding.
    pytest.param(5, [0,1,2,2,3,4], [(0,1,2),(3,2,4)], id='strip-degenerate'),
    pytest.param(6, None, [(0,1,2),(0,2,3),(0,3,4),(0,4,5)], id='fan-unindexed'),
    pytest.param(6, [2,3,4,5,0,1], [(2,3,4),(2,4,5),(2,5,0),(2,0,1)], id='fan-center'),
]


def _write_source(path, mode, indices, *, atlas=False, reflected=False, index_kind='valid'):
    coords = ([[0,0,0],[0,1,0],[1,0,0],[1,1,0],[2,0,0],[2,1,0]] if mode == 5 else
              [[0,0,0],[1,0,0],[1,1,0],[0,2,0],[-1,1,0],[-1,0,0]])
    positions = np.array(coords, '<f4')
    normals = np.tile(np.array([0,0,1], '<f4'), (6,1))
    uvs = ((positions[:,:2] + 2) / 5).astype('<f4')
    chunks = [a.tobytes() for a in (positions,normals,uvs)]
    accessors = [{'bufferView': i, 'componentType': 5126, 'count': 6, 'type': kind}
                 for i,kind in enumerate(('VEC3','VEC3','VEC2'))]
    prim = {'attributes': {'POSITION':0,'NORMAL':1,'TEXCOORD_0':2}, 'mode':mode,'material':0}
    if indices is not None:
        dtype, component, kind = '<u2',5123,'SCALAR'
        if index_kind == 'float':
            dtype,component = '<f4',5126
        elif index_kind == 'signed':
            dtype,component = '<i2',5122
        elif index_kind == 'vector':
            kind,indices = 'VEC2',[[i,i] for i in indices]
        chunks.append(np.asarray(indices,dtype=dtype).tobytes())
        accessors.append({'bufferView':3,'componentType':component,'count':len(indices),
                          'type':kind,'normalized':index_kind == 'normalized'})
        prim['indices'] = 3
    offsets = np.cumsum([0]+[len(c) for c in chunks[:-1]]).tolist()
    raw = b''.join(chunks)
    material = {'name':'stone.dds','pbrMetallicRoughness':{'baseColorFactor':[.2,.4,.6,1]}}
    doc = {'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],
           'nodes':[{'mesh':0,'scale':[-2 if reflected else 2,3,1],'translation':[4,5,6]}],
           'meshes':[{'primitives':[prim]}],'materials':[material],
           'buffers':[{'uri':'data:application/octet-stream;base64,'+base64.b64encode(raw).decode(),
                       'byteLength':len(raw)}],
           'bufferViews':[{'buffer':0,'byteOffset':off,'byteLength':len(c)}
                          for off,c in zip(offsets,chunks)],'accessors':accessors}
    if atlas:
        from tests.test_package_bake import _png, _uri
        doc['images'] = [{'uri':_uri(_png(np.full((4,4,4),255,dtype=np.uint8)),'image/png')}]
        doc['textures'] = [{'source':0}]
        material['occlusionTexture'] = {'index':0}
    path.write_text(json.dumps(doc))
    return positions * [-2 if reflected else 2,3,1] + [4,5,6], normals, uvs


def _surface(positions, triangles):
    result = Counter()
    for triangle in triangles:
        corners = tuple(tuple(np.round(positions[i],4)) for i in triangle)
        result[min(corners,corners[1:]+corners[:1],corners[2:]+corners[:2])] += 1
    return result


def _expected(faces, reflected):
    return [(a,c,b) for a,b,c in faces] if reflected else faces


def _package(source,target):
    return subprocess.run([sys.executable,'-m','any2nif',str(source),str(target),'--package',
                           '--collision','none','--bake-size','64'], capture_output=True,text=True,
                          cwd=Path(__file__).resolve().parents[1])


def _snapshot(target):
    return {p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mode,indices,faces',CASES)
@pytest.mark.parametrize('reflected',[False,True])
def test_reader_complete_surface_and_attributes(tmp_path,mode,indices,faces,reflected):
    source = tmp_path/'surface.gltf'
    points,normals,uvs = _write_source(source,mode,indices,reflected=reflected)
    meshes = read_gltf(str(source))
    assert len(meshes) == 1
    mesh = meshes[0]
    assert _surface(mesh.positions,mesh.triangles) == _surface(points,_expected(faces,reflected))
    np.testing.assert_allclose(mesh.positions,points)
    np.testing.assert_allclose(mesh.normals,normals)
    np.testing.assert_allclose(mesh.uvs,uvs)
    assert mesh.material == 'stone'
    assert mesh.material_index == 0


@pytest.mark.parametrize('mode,indices,faces',CASES)
@pytest.mark.parametrize('atlas,reflected',[(False,False),(True,True)])
def test_package_nif_complete_oriented_surface(tmp_path,mode,indices,faces,atlas,reflected):
    source,target = tmp_path/'surface.gltf',tmp_path/'Data'
    points,_,_ = _write_source(source,mode,indices,atlas=atlas,reflected=reflected)
    result = _package(source,target)
    assert result.returncode == 0,result.stderr
    manifest = json.loads((target/MANIFEST).read_text())
    meshes = read_nif((target/manifest['mesh']).read_bytes())
    assert len(meshes) == 1
    mesh = meshes[0]
    assert _surface(np.asarray(mesh.positions)/SKYRIM_UNITS_PER_METRE,mesh.triangles) == (
        _surface(points,_expected(faces,reflected)))
    np.testing.assert_allclose(mesh.normals, np.tile([0,0,1], (len(mesh.positions),1)), atol=.02)
    assert len(mesh.uvs) == len(mesh.positions)
    assert manifest['textures']


@pytest.mark.parametrize('mode',[4,5,6])
@pytest.mark.parametrize('bad',['range','float','signed','vector','normalized'])
@pytest.mark.parametrize('atlas',[False,True])
def test_bad_indices_keep_old_package_bytes(tmp_path,mode,bad,atlas):
    source,target = tmp_path/'surface.gltf',tmp_path/'Data'
    _write_source(source,4,[0,1,2],atlas=atlas)
    result = _package(source,target)
    assert result.returncode == 0,result.stderr
    before = _snapshot(target)
    _write_source(source,mode,[0,1,99 if bad == 'range' else 2],atlas=atlas,index_kind=bad)
    with pytest.raises(GltfError):
        read_gltf(str(source))
    result = _package(source,target)
    assert result.returncode == 2,result.stderr
    assert _snapshot(target) == before


@pytest.mark.parametrize('atlas',[False,True])
def test_mixed_primitives_materials_and_nodes_keep_all_surfaces(tmp_path,atlas):
    source,target = tmp_path/'mixed.gltf',tmp_path/'Data'
    points,_,_ = _write_source(source,6,None,atlas=atlas)
    doc = json.loads(source.read_text())
    original = doc['meshes'][0]['primitives'][0]
    doc['meshes'] = []
    doc['nodes'] = []
    doc['materials'] = [dict(doc['materials'][0],name=f'material_{i}') for i in range(3)]
    expected_faces = [[(0,1,2),(3,4,5)],
                      [(0,1,2),(2,1,3),(2,3,4),(4,3,5)],
                      [(0,1,2),(0,2,3),(0,3,4),(0,4,5)]]
    for i,mode in enumerate((4,5,6)):
        doc['meshes'].append({'primitives':[dict(original,mode=mode,material=i)]})
        doc['nodes'].append({'mesh':i,'scale':[2,3,1],'translation':[4+i*10,5,6]})
    doc['scenes'][0]['nodes'] = [0,1,2]
    source.write_text(json.dumps(doc))
    expected = Counter()
    for i,faces in enumerate(expected_faces):
        expected.update(_surface(points+[i*10,0,0],faces))
    reader_meshes = read_gltf(str(source))
    assert [mesh.material_index for mesh in reader_meshes] == [0,1,2]
    actual = Counter()
    for mesh in reader_meshes:
        actual.update(_surface(mesh.positions,mesh.triangles))
    assert actual == expected
    result = _package(source,target)
    assert result.returncode == 0,result.stderr
    manifest = json.loads((target/MANIFEST).read_text())
    meshes = read_nif((target/manifest['mesh']).read_bytes())
    assert len(meshes) == 3
    actual = Counter()
    for mesh in meshes:
        actual.update(_surface(np.asarray(mesh.positions)/SKYRIM_UNITS_PER_METRE,mesh.triangles))
    assert actual == expected
    assert len(manifest['textures']) == 3


@pytest.mark.parametrize('mode,atlas',[(5,False),(6,True)])
def test_bad_indices_never_publish_fresh_target(tmp_path,mode,atlas):
    source,target = tmp_path/'invalid.gltf',tmp_path/'Data'
    _write_source(source,mode,[0,1,99],atlas=atlas)
    result = _package(source,target)
    assert result.returncode == 2,result.stderr
    assert not target.exists()

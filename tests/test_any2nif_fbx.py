"""FBX normalisation through the external FBX2glTF binary."""

from __future__ import annotations

from pathlib import Path

import pytest

from any2nif import fbx_backend
from any2nif.errors import AnyError
from gltf2nif import read_gltf


ASCII_TETRAHEDRON = """; FBX 7.4.0 project file
FBXHeaderExtension:  {
    FBXHeaderVersion: 1003
    FBXVersion: 7400
    Creator: "any2nif fixture"
}
GlobalSettings:  {
    Version: 1000
    Properties70:  {
        P: "UpAxis", "int", "Integer", "",1
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",2
        P: "FrontAxisSign", "int", "Integer", "",1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",1
    }
}
Definitions:  {
    Version: 100
    Count: 3
    ObjectType: "Geometry" {
        Count: 1
    }
    ObjectType: "Model" {
        Count: 1
    }
    ObjectType: "Material" {
        Count: 1
    }
}
Objects:  {
    Geometry: 100000, "Geometry::tet", "Mesh" {
        Vertices: *12 {
            a: 0,0,0,2,0,0,0,3,0,0,0,4
        }
        PolygonVertexIndex: *12 {
            a: 0,2,-2,0,1,-4,0,3,-3,1,2,-4
        }
        GeometryVersion: 124
        LayerElementNormal: 0 {
            Version: 102
            Name: ""
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "Direct"
            Normals: *36 {
                a: 0,0,-1,0,0,-1,0,0,-1,0,-1,0,0,-1,0,0,-1,0,-1,0,0,-1,0,0,-1,0,0,0.5773,0.5773,0.5773,0.5773,0.5773,0.5773,0.5773,0.5773,0.5773
            }
        }
        LayerElementUV: 0 {
            Version: 101
            Name: "UVMap"
            MappingInformationType: "ByPolygonVertex"
            ReferenceInformationType: "IndexToDirect"
            UV: *8 {
                a: 0,0,1,0,0,1,1,1
            }
            UVIndex: *12 {
                a: 0,1,2,0,1,3,0,3,2,1,2,3
            }
        }
        LayerElementMaterial: 0 {
            Version: 101
            Name: ""
            MappingInformationType: "AllSame"
            ReferenceInformationType: "IndexToDirect"
            Materials: *1 {
                a: 0
            }
        }
        Layer: 0 {
            Version: 100
            LayerElement:  {
                Type: "LayerElementNormal"
                TypedIndex: 0
            }
            LayerElement:  {
                Type: "LayerElementMaterial"
                TypedIndex: 0
            }
            LayerElement:  {
                Type: "LayerElementUV"
                TypedIndex: 0
            }
        }
    }
    Model: 200000, "Model::tet", "Mesh" {
        Version: 232
        Properties70:  {
            P: "DefaultAttributeIndex", "int", "Integer", "",0
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,0
        }
        Shading: T
        Culling: "CullingOff"
    }
    Material: 300000, "Material::rock01", "" {
        Version: 102
        ShadingModel: "phong"
        MultiLayer: 0
        Properties70:  {
            P: "DiffuseColor", "Color", "", "A",0.8,0.7,0.6
            P: "SpecularColor", "Color", "", "A",0.2,0.2,0.2
            P: "Shininess", "double", "Number", "",20
        }
    }
}
Connections:  {
    C: "OO",100000,200000
    C: "OO",300000,200000
    C: "OO",200000,0
}
"""


def _executable(path: Path, body: str = "exit 0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_find_fbx2gltf_explicit_precedes_everything(tmp_path, monkeypatch):
    explicit = _executable(tmp_path / "explicit")
    environment = _executable(tmp_path / "environment")
    monkeypatch.setenv("MODEL_CONVERTER_FBX2GLTF", str(environment))

    assert fbx_backend.find_fbx2gltf(str(explicit)) == str(explicit)


def test_find_fbx2gltf_environment_precedes_repo_and_path(tmp_path, monkeypatch):
    environment = _executable(tmp_path / "environment")
    path_binary = _executable(tmp_path / "path" / "FBX2glTF")
    monkeypatch.setenv("MODEL_CONVERTER_FBX2GLTF", str(environment))
    monkeypatch.setenv("PATH", str(path_binary.parent))

    assert fbx_backend.find_fbx2gltf() == str(environment)


def test_find_fbx2gltf_repo_precedes_path(tmp_path, monkeypatch):
    fake_repo = tmp_path / "repo"
    repo_binary = _executable(fake_repo / "tools" / "bin" / "FBX2glTF")
    path_binary = _executable(tmp_path / "path" / "FBX2glTF")
    monkeypatch.delenv("MODEL_CONVERTER_FBX2GLTF", raising=False)
    monkeypatch.setenv("PATH", str(path_binary.parent))
    monkeypatch.setattr(
        fbx_backend, "__file__", str(fake_repo / "any2nif" / "fbx_backend.py")
    )

    assert fbx_backend.find_fbx2gltf() == str(repo_binary)


def test_find_fbx2gltf_uses_path_names_in_order(tmp_path, monkeypatch):
    path_dir = tmp_path / "path"
    preferred = _executable(path_dir / "FBX2glTF")
    _executable(path_dir / "FBX2glTF-linux-x64")
    monkeypatch.delenv("MODEL_CONVERTER_FBX2GLTF", raising=False)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(
        fbx_backend, "__file__", str(tmp_path / "empty-repo" / "any2nif" / "fbx_backend.py")
    )

    assert fbx_backend.find_fbx2gltf() == str(preferred)


def test_missing_tool_is_actionable_code_1(tmp_path, monkeypatch):
    monkeypatch.setattr(fbx_backend, "find_fbx2gltf", lambda explicit=None: None)

    with pytest.raises(AnyError) as excinfo:
        fbx_backend.to_gltf(str(tmp_path / "mesh.fbx"), str(tmp_path / "work"))

    assert excinfo.value.code == 1
    message = str(excinfo.value)
    assert "MODEL_CONVERTER_FBX2GLTF=<path>" in message
    assert "tools/fetch_fbx2gltf.sh" in message
    assert "github.com/facebookincubator/FBX2glTF" in message


def test_bad_fbx_converter_failure_is_code_2(tmp_path):
    source = tmp_path / "broken.fbx"
    source.write_text("not an FBX", encoding="utf-8")
    converter = _executable(
        tmp_path / "failing-FBX2glTF",
        'echo "first diagnostic" >&2\necho "bad FBX" >&2\nexit 7',
    )

    with pytest.raises(AnyError) as excinfo:
        fbx_backend.to_gltf(str(source), str(tmp_path / "work"), fbx2gltf=str(converter))

    assert excinfo.value.code == 2
    assert "FBX2glTF failed (exit 7)" in str(excinfo.value)
    assert "bad FBX" in str(excinfo.value)


def test_empty_converter_output_is_code_2(tmp_path):
    source = tmp_path / "mesh.fbx"
    source.write_text(ASCII_TETRAHEDRON, encoding="utf-8")
    converter = _executable(
        tmp_path / "empty-FBX2glTF",
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--output" ]; then shift; : > "$1.glb"; exit 0; fi\n'
        '  shift\n'
        'done\nexit 0',
    )

    with pytest.raises(AnyError) as excinfo:
        fbx_backend.to_gltf(str(source), str(tmp_path / "work"), fbx2gltf=str(converter))

    assert excinfo.value.code == 2
    assert "non-empty .glb" in str(excinfo.value)


@pytest.mark.skipif(fbx_backend.find_fbx2gltf() is None, reason="FBX2glTF not installed")
def test_ascii_fbx_real_conversion_has_expected_geometry(tmp_path):
    source = tmp_path / "tetrahedron.fbx"
    source.write_text(ASCII_TETRAHEDRON, encoding="utf-8")

    glb = fbx_backend.to_gltf(str(source), str(tmp_path / "work"))
    meshes = read_gltf(glb)

    assert len(meshes) == 1
    assert len(meshes[0].positions) == 12
    assert len(meshes[0].triangles) == 4
    assert meshes[0].material == "rock01"

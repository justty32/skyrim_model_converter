"""Complete Data package contracts through the production CLI and binary reader."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST, texture_references
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif
from tests.gltf2nif_fixtures import CUBE_POS, CUBE_TRIS, write_gltf_interleaved
from tests.tex2dds_fixtures import flat_rgb, png_bytes


def _source(tmp_path, *, material_name="../unsafe"):
    source = tmp_path / "crate.gltf"
    write_gltf_interleaved(source.as_posix(), [{
        "positions": CUBE_POS, "normals": [(0, 1, 0)] * 8,
        "uvs": [(0, 0)] * 8, "triangles": CUBE_TRIS, "material": material_name,
    }])
    doc = json.loads(source.read_text())
    payload = base64.b64encode(png_bytes(flat_rgb(8, (240, 50, 20)))).decode()
    doc["images"] = [{"uri": f"data:image/png;base64,{payload}"}]
    doc["textures"] = [{"source": 0}]
    doc["materials"][0]["pbrMetallicRoughness"] = {"baseColorTexture": {"index": 0}}
    source.write_text(json.dumps(doc))
    return source


def _snapshot(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


def test_one_command_produces_self_contained_package(tmp_path):
    source = _source(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "data"
    result = subprocess.run([sys.executable, "-m", "any2nif", str(source), str(output), "--package"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / MANIFEST).read_text())
    data = (output / manifest["mesh"]).read_bytes()
    assert len(read_nif(data)[0].triangles) == 12
    assert "bhkConvexVerticesShape" in _read_header(_Reader(data))["types"]
    assert manifest["collision"] == "convex"
    refs = texture_references(data)
    assert refs and refs == manifest["textures"]
    for ref in refs:
        path = output / ref.replace("\\", "/")
        with Image.open(path) as image:
            image.load()
            assert image.format == "DDS"
            pixel = np.asarray(image.convert("RGB"))[0, 0]
            np.testing.assert_allclose(pixel, [240, 50, 20], atol=8)
    for path, expected in manifest["sha256"].items():
        assert hashlib.sha256((output / path).read_bytes()).hexdigest() == expected
    assert source.read_bytes() == original
    assert not list(output.rglob("*.gltf"))
    assert not list(tmp_path.glob(".any2nif-package-*"))


def test_bad_source_preserves_known_good_package(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package", "--collision", "box"]) == 0
    before = _snapshot(output)
    doc = json.loads(source.read_text())
    doc["images"][0] = {"uri": "missing.png"}
    source.write_text(json.dumps(doc))
    assert main([str(source), str(output), "--package"]) != 0
    assert _snapshot(output) == before
    assert not list(tmp_path.glob(".any2nif-package-*"))


def test_malformed_pbr_is_not_silently_replaced_with_defaults(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    original = _snapshot(output)
    doc = json.loads(source.read_text())
    doc["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = "broken"
    source.write_text(json.dumps(doc))
    assert main([str(source), str(output), "--package"]) == 2
    assert _snapshot(output) == original


def test_regeneration_replaces_whole_owned_package(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package", "--asset-name", "old"]) == 0
    assert main([str(source), str(output), "--package", "--asset-name", "new", "--collision", "none"]) == 0
    assert not (output / "meshes/any2nif/old").exists()
    manifest = json.loads((output / MANIFEST).read_text())
    assert manifest["mesh"] == "meshes/any2nif/new/new.nif"
    assert "bhkRigidBody" not in _read_header(_Reader((output / manifest["mesh"]).read_bytes()))["types"]


def test_user_changes_and_unowned_directory_are_preserved(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "data"
    output.mkdir()
    user_file = output / "notes.txt"
    user_file.write_text("keep")
    assert main([str(source), str(output), "--package"]) == 1
    assert user_file.read_text() == "keep"
    owned = tmp_path / "owned"
    assert main([str(source), str(owned), "--package"]) == 0
    (owned / "notes.txt").write_text("keep")
    before = _snapshot(owned)
    assert main([str(source), str(owned), "--package"]) == 1
    assert _snapshot(owned) == before


def test_publish_error_rolls_back_existing_package(tmp_path, monkeypatch):
    from any2nif import package

    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    before = _snapshot(output)
    real_replace = package.os.replace

    def fail_publish(src, dst):
        if Path(src).name == "data" and Path(dst) == output:
            raise OSError("simulated publish failure")
        return real_replace(src, dst)

    monkeypatch.setattr(package.os, "replace", fail_publish)
    assert main([str(source), str(output), "--package"]) == 1
    assert _snapshot(output) == before


@pytest.mark.parametrize("option", [["--textures-out", "elsewhere"], ["--texprefix", "elsewhere"],
                                    ["--keep-intermediate", "elsewhere"]])
def test_package_refuses_conflicting_path_options(tmp_path, option):
    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package", *option]) == 1
    assert not output.exists()


@pytest.mark.parametrize("extension", ["obj", "glb", "stl", "ply", "dae"])
def test_common_formats_produce_model_texture_and_collision(tmp_path, extension):
    from tests.any2nif_fixtures import make_textured_box

    source = tmp_path / f"model.{extension}"
    make_textured_box().export(str(source))
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    data = (output / manifest["mesh"]).read_bytes()
    assert sum(len(mesh.triangles) for mesh in read_nif(data)) == 12
    assert texture_references(data)
    assert "bhkConvexVerticesShape" in _read_header(_Reader(data))["types"]


def test_real_fbx_package(tmp_path):
    from any2nif.fbx_backend import find_fbx2gltf
    from tests.test_any2nif_fbx import ASCII_TETRAHEDRON

    if find_fbx2gltf() is None:
        pytest.skip("optional FBX2glTF binary unavailable")
    source = tmp_path / "tetra.fbx"
    source.write_text(ASCII_TETRAHEDRON)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    data = (output / manifest["mesh"]).read_bytes()
    assert sum(len(mesh.triangles) for mesh in read_nif(data)) == 4
    assert texture_references(data)
    assert "bhkConvexVerticesShape" in _read_header(_Reader(data))["types"]


def test_all_texture_slots_exist_and_are_referenced(tmp_path):
    source = _source(tmp_path)
    doc = json.loads(source.read_text())
    material = doc["materials"][0]
    material["normalTexture"] = {"index": 0}
    material["emissiveTexture"] = {"index": 0}
    material["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {"index": 0}
    source.write_text(json.dumps(doc))
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    refs = manifest["textures"]
    assert len(refs) == 4
    for suffix in ("_n.dds", "_s.dds", "_g.dds"):
        assert any(path.endswith(suffix) for path in refs)


def test_cleanup_failure_reports_success_and_preserves_backup(tmp_path, monkeypatch, capsys):
    from any2nif import package

    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    original = _snapshot(output)
    real_rmtree = package.shutil.rmtree

    def fail_old_cleanup(path, *args, **kwargs):
        if ".previous-" in str(path):
            raise PermissionError("simulated cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(package.shutil, "rmtree", fail_old_cleanup)
    assert main([str(source), str(output), "--package", "--asset-name", "replacement"]) == 0
    backup, = tmp_path.glob(".data.previous-*")
    assert _snapshot(backup) == original
    assert "new package published" in capsys.readouterr().err
    assert (output / "meshes/any2nif/replacement/replacement.nif").exists()


def test_failed_publish_and_restore_keeps_backup(tmp_path, monkeypatch, capsys):
    from any2nif import package

    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    original = _snapshot(output)
    real_replace = package.os.replace

    def fail_destination(src, dst):
        if Path(dst) == output:
            raise OSError("simulated destination unavailable")
        return real_replace(src, dst)

    monkeypatch.setattr(package.os, "replace", fail_destination)
    assert main([str(source), str(output), "--package"]) == 1
    backup, = tmp_path.glob(".data.previous-*")
    assert _snapshot(backup) == original
    assert str(backup) in capsys.readouterr().err


@pytest.mark.parametrize("name", ["con", "NUL", "com1", "lpt9"])
def test_windows_reserved_names_are_rejected(tmp_path, name):
    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package", "--asset-name", name]) == 1
    assert not output.exists()


def test_source_and_output_symlink_protection(tmp_path):
    source = _source(tmp_path)
    original = source.read_bytes()
    for destination in (source, tmp_path):
        assert main([str(source), str(destination), "--package"]) == 1
        assert source.read_bytes() == original
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable")
    assert main([str(source), str(link), "--package"]) == 1
    assert link.is_symlink() and not list(target.iterdir())


def test_package_preserves_parser_and_skin_exit_codes(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "data"
    doc = json.loads(source.read_text())
    source.write_text("{bad json")
    assert main([str(source), str(output), "--package"]) == 2
    doc["nodes"][0]["skin"] = 0
    doc["skins"] = [{"joints": [0]}]
    source.write_text(json.dumps(doc))
    assert main([str(source), str(output), "--package"]) == 3
    assert not output.exists()


def test_validation_rejects_readable_but_incomplete_dds(tmp_path):
    from any2nif.errors import AnyError
    from any2nif.package import _validate
    from tex2dds import encode_dds

    source = _source(tmp_path)
    output = tmp_path / "data"
    assert main([str(source), str(output), "--package"]) == 0
    manifest = json.loads((output / MANIFEST).read_text())
    texture = output / manifest["textures"][0].replace("\\", "/")
    texture.write_bytes(encode_dds(flat_rgb(8, (200, 40, 40)), mipmaps=False))
    with Image.open(texture) as image:
        image.load()  # Independent decoder accepts base-only DDS.
    with pytest.raises(AnyError, match="incomplete DDS mip chain"):
        _validate(output, output / manifest["mesh"])

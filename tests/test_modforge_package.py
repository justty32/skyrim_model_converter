"""Live producer/consumer contract and failure protection for the one-static slice."""

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image
import pytest

from modforge_package import cli, safety, validation
from nif2gltf.nif_reader import read_nif
from tools.build_modforge_box import build

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def live_package():
    dll = os.environ.get("MODFORGE_DLL")
    dotnet = os.environ.get("MODFORGE_DOTNET", "dotnet")
    if not dll or not Path(dll).is_file():
        pytest.skip("set MODFORGE_DLL to the freshly built production CLI for the live contract")
    backend = ROOT / "backend"
    backend.mkdir(exist_ok=True)
    # WSL + Windows dotnet needs its writable work tree on a Windows-mounted drive.
    with tempfile.TemporaryDirectory(prefix="modforge-contract-", dir=backend) as temporary:
        work = Path(temporary)
        source, output = build(work / "box.glb"), work / "Data"
        args = [str(source), str(output), "--modforge-dll", str(Path(dll).absolute()), "--dotnet", dotnet]
        result = subprocess.run([sys.executable, "-m", "modforge_package", *args],
                                cwd=ROOT, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        yield source, output, args


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_live_glb_esp_nif_dds_and_rebuild(live_package):
    source, output, args = live_package
    before = snapshot(output)
    manifest = json.loads((output / safety.MANIFEST).read_text())
    assert manifest["spec"] == {"pluginName": "SyntheticBox.esp", "esl": False,
        "statics": [{"editorId": "SyntheticBox", "model": "any2nif/synthetic_box/synthetic_box.nif"}]}
    assert manifest["static"] == {"formKey": "SyntheticBox.esp:0x000800", "recordType": "Static",
        "editorId": "SyntheticBox", "modelPath": "any2nif/synthetic_box/synthetic_box.nif"}
    assert manifest["sourceSha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["sha256"] == safety.hashes(output, (safety.MANIFEST,))
    nif = (output / "Meshes/any2nif/synthetic_box/synthetic_box.nif").read_bytes()
    meshes = read_nif(nif)
    assert sum(len(m.triangles) for m in meshes) == 12
    points = np.array([p for m in meshes for p in m.positions]) / 70.03
    np.testing.assert_allclose(points.min(axis=0), [1, 2, 3], atol=1e-5)
    np.testing.assert_allclose(points.max(axis=0), [2, 4, 7], atol=1e-5)
    assert manifest["collision"] == {"corners": 8, "min": [1, -7, 2], "max": [2, -3, 4]}
    with Image.open(output / "Textures/any2nif/synthetic_box/material_0000.dds") as image:
        image.load()
        assert image.size == (8, 8)
        np.testing.assert_allclose(image.convert("RGB").getpixel((0, 0)), [224, 72, 24], atol=8)
    assert cli.main(args) == 0
    assert snapshot(output) == before
    assert not list(output.parent.glob(".modforge-model-*"))


@pytest.mark.parametrize("failure", ["converter", "package", "omitted-dds", "bad-readback", "unsafe-manifest"])
def test_failure_preserves_existing_complete_package(live_package, monkeypatch, failure):
    source, output, args = live_package
    before, source_bytes = snapshot(output), source.read_bytes()
    original = cli.run

    def failed_run(argv):
        if failure == "package" and "package" in argv:
            raise ValueError("injected ModForge package failure")
        result = original(argv)
        if failure == "unsafe-manifest" and "any2nif" in argv:
            marker_path = Path(argv[4]) / "converter-package.json"
            marker = json.loads(marker_path.read_text())
            marker["mesh"] = "meshes/../../../outside.nif"
            marker_path.write_text(json.dumps(marker))
        if failure == "omitted-dds" and "package" in argv:
            staged = Path(argv[argv.index("package") + 2])
            if str(staged).startswith("C:"):
                staged = Path(subprocess.check_output(["wslpath", "-u", str(staged)], text=True).strip())
            next(staged.rglob("*.dds")).unlink()
        if failure == "bad-readback" and "export-json" in argv:
            exported = Path(argv[-1])
            if str(exported).startswith("C:"):
                exported = Path(subprocess.check_output(["wslpath", "-u", str(exported)], text=True).strip())
            document = json.loads(exported.read_text())
            document["records"][0]["modelPath"] = "textures/wrong.nif"
            exported.write_text(json.dumps(document))
        return result

    try:
        if failure == "converter":
            # A syntactically readable GLB with missing geometry reaches the real producer.
            from pygltflib import GLTF2
            doc = GLTF2().load_binary(str(source))
            doc.meshes[0].primitives[0].attributes.POSITION = 99
            doc.save_binary(str(source))
        monkeypatch.setattr(cli, "run", failed_run)
        assert cli.main(args) != 0
        assert snapshot(output) == before
        assert not list(output.parent.glob(".modforge-model-*"))
    finally:
        source.write_bytes(source_bytes)


@pytest.mark.parametrize("path", ["../bad", "/bad", "C:/bad", "a//b", "textures/../bad",
                                   "//host/share", "a:stream", "a/b.", "a/b ", "a/CON.txt"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError):
        safety.relative_path(path)


def test_source_output_and_ownership_guards(tmp_path):
    source = build(tmp_path / "box.glb")
    with pytest.raises(ValueError, match="contain the source"):
        safety.check_destination(tmp_path, source)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "user.txt").write_text("preserve me")
    with pytest.raises(ValueError, match="not an owned"):
        safety.check_destination(output, source)
    before = snapshot(output)
    (output / safety.MANIFEST).write_text(json.dumps({"schema": safety.SCHEMA, "sha256": {}}))
    with pytest.raises(ValueError, match="user changes"):
        safety.check_destination(output, source)
    assert (output / "user.txt").read_bytes() == before["user.txt"]


@pytest.mark.parametrize("marker", [[], None])
def test_non_object_ownership_manifest_rejected(tmp_path, marker):
    output = tmp_path / "Data"
    output.mkdir()
    (output / safety.MANIFEST).write_text(json.dumps(marker))
    with pytest.raises(ValueError, match="unowned"):
        safety.check_destination(output, tmp_path / "source.glb")


def test_dotdot_containment_and_missing_tool_preserve_output(live_package):
    source, output, args = live_package
    before = snapshot(output)
    unsafe = args.copy()
    unsafe[1] = str(source.parent / "not-created" / "..")
    assert cli.main(unsafe) != 0
    assert source.is_file() and snapshot(output) == before
    missing_tool = args.copy()
    missing_tool[-1] = "definitely-nonexistent-dotnet"
    assert cli.main(missing_tool) != 0
    assert snapshot(output) == before


def test_scratch_cleanup_after_publish_warns_success(tmp_path, monkeypatch, capsys):
    original = safety.tempfile.TemporaryDirectory.cleanup

    def fail(temporary):
        original(temporary)
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(safety.tempfile.TemporaryDirectory, "cleanup", fail)
    with safety.staging_directory(tmp_path) as state:
        state["published"] = True
    assert "published; scratch cleanup failed" in capsys.readouterr().err


def test_symlink_source_and_destination_rejected(tmp_path):
    source = build(tmp_path / "box.glb")
    link = tmp_path / "link"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation not available")
    with pytest.raises(ValueError, match="symlink"):
        safety.reject_links(link)
    directory_link = tmp_path / "dirlink"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        safety.reject_links(directory_link / "new")


def test_windows_reparse_attribute_rejected_without_following_target(tmp_path, monkeypatch):
    from types import SimpleNamespace
    junction = tmp_path / "junction"
    junction.mkdir()
    original = Path.lstat

    def metadata(path):
        if path == junction:
            return SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)
        return original(path)

    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(ValueError, match="reparse"):
        safety.reject_links(junction / "new")


@pytest.mark.parametrize("restore_fails", [False, True])
def test_publish_failure_restores_or_preserves_backup(tmp_path, monkeypatch, restore_fails):
    old, staged = tmp_path / "Data", tmp_path / "stage"
    old.mkdir()
    staged.mkdir()
    (old / "old.txt").write_text("complete old bytes")
    (staged / "new.txt").write_text("complete new bytes")
    replace = os.replace

    def fail(source, destination):
        if source == staged or (restore_fails and ".previous-" in source.name):
            raise OSError("injected rename failure")
        return replace(source, destination)

    monkeypatch.setattr(safety.os, "replace", fail)
    with pytest.raises(OSError):
        safety.publish(staged, old)
    backup = list(tmp_path.glob(".Data.previous-*"))
    assert not (old / "new.txt").exists()
    if restore_fails:
        assert len(backup) == 1
        assert (backup[0] / "old.txt").read_text() == "complete old bytes"
    else:
        assert not backup
        assert (old / "old.txt").read_text() == "complete old bytes"


def test_glb_external_resource_rejected(tmp_path):
    from pygltflib import GLTF2
    source = build(tmp_path / "box.glb")
    doc = GLTF2().load_binary(str(source))
    doc.images[0].uri = "../private.png"
    doc.save_binary(str(source))
    with pytest.raises(ValueError, match="external resource"):
        validation.embedded_glb(source)


def test_box_validation_rejects_broken_link_and_corners(live_package):
    _, output, _ = live_package
    from nif2gltf._binreader import _Reader
    from nif2gltf.nif_reader import _read_header
    data = bytearray((output / "Meshes/any2nif/synthetic_box/synthetic_box.nif").read_bytes())
    header = _read_header(_Reader(data))
    broken = data.copy()
    root = header["offsets"][0]
    extra_count = struct.unpack_from("<I", data, root + 4)[0]
    struct.pack_into("<i", broken, root + 68 + extra_count * 4, -1)
    with pytest.raises(ValueError, match="collision link"):
        validation.box_collision(broken)
    shape = header["offsets"][header["types"].index("bhkConvexVerticesShape")]
    struct.pack_into("<f", data, shape + 36, 99)
    with pytest.raises(ValueError, match="bounds"):
        validation.box_collision(data)


def test_real_producer_box_minimum_thickness(tmp_path):
    from pygltflib import GLTF2
    source = build(tmp_path / "thin.glb")
    doc = GLTF2().load_binary(str(source))
    binary = bytearray(doc.binary_blob())
    for i in range(8):
        struct.pack_into("<f", binary, i * 32 + 4, 2.0)
    doc.set_binary_blob(bytes(binary))
    doc.save_binary(str(source))
    converted = tmp_path / "converted"
    cli.run([sys.executable, "-m", "any2nif", source, converted,
             "--package", "--collision", "box", "--asset-name", "thin"])
    _, collision = validation.converter_assets(converted, "thin")
    np.testing.assert_allclose(collision["min"], [1, -7, 1.975])
    np.testing.assert_allclose(collision["max"], [2, -3, 2.025])

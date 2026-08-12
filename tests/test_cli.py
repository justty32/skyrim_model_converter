"""CLI end-to-end: synthetic .nif -> .gltf, exit-code contract per PROTOCOL.md."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from pygltflib import GLTF2

from nif2gltf.cli import main
from tests.nif_fixtures import build_le_nif

TRI = [(0, 1, 2)]
VERTS = [(1.0, 2.0, 3.0), (4.0, 0.0, 0.0), (0.0, 5.0, 0.0)]
NORMALS = [(0.0, 0.0, 1.0)] * 3
UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]


def _write_nif(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def test_single_file_conversion(tmp_path):
    nif = str(tmp_path / "rock.nif")
    out = str(tmp_path / "rock.gltf")
    _write_nif(nif, build_le_nif(VERTS, NORMALS, UVS, TRI))
    assert main(["--in", nif, "--out", out, "--flat"]) == 0
    assert os.path.exists(out)
    assert os.path.exists(str(tmp_path / "rock.bin"))
    gltf = GLTF2().load(out)
    assert len(gltf.meshes) == 1


def test_missing_args_returns_1(tmp_path):
    assert main(["--flat"]) == 1


def test_missing_source_returns_1(tmp_path):
    out = str(tmp_path / "x.gltf")
    assert main(["--in", str(tmp_path / "nope.nif"), "--out", out]) == 1


def test_output_failure_returns_1_without_traceback(tmp_path, capsys):
    nif = str(tmp_path / "rock.nif")
    _write_nif(nif, build_le_nif(VERTS, NORMALS, UVS, TRI))
    with patch("nif2gltf.cli.write_gltf", side_effect=OSError("disk full")):
        assert main(["--in", nif, "--out", str(tmp_path / "rock.gltf"), "--flat"]) == 1
    assert "error: writing glTF" in capsys.readouterr().err


def test_bad_version_returns_2(tmp_path):
    nif = str(tmp_path / "bad.nif")
    data = bytearray(build_le_nif(VERTS, NORMALS, UVS, TRI))
    nl = data.index(0x0A)
    data[nl + 1:nl + 5] = (0x14010003).to_bytes(4, "little")
    _write_nif(nif, bytes(data))
    assert main(["--in", nif, "--out", str(tmp_path / "bad.gltf")]) == 2


def test_textures_flag_rejected(tmp_path):
    nif = str(tmp_path / "rock.nif")
    _write_nif(nif, build_le_nif(VERTS, NORMALS, UVS, TRI))
    assert main(["--in", nif, "--out", str(tmp_path / "rock.gltf"),
                 "--textures", str(tmp_path)]) == 1


def test_manifest_batch(tmp_path):
    nif_a = str(tmp_path / "a.nif")
    nif_b = str(tmp_path / "b.nif")
    _write_nif(nif_a, build_le_nif(VERTS, NORMALS, UVS, TRI))
    _write_nif(nif_b, build_le_nif(VERTS, NORMALS, UVS, TRI))
    manifest = str(tmp_path / "work.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "items": [
            {"in": nif_a, "out": "a.gltf"},
            {"in": nif_b, "out": "b.gltf"},
        ]}, fh)
    outdir = str(tmp_path / "out")
    assert main(["--manifest", manifest, "--outdir", outdir]) == 0
    assert os.path.exists(os.path.join(outdir, "a.gltf"))
    assert os.path.exists(os.path.join(outdir, "b.gltf"))


def test_manifest_partial_failure_returns_1(tmp_path):
    nif_a = str(tmp_path / "a.nif")
    _write_nif(nif_a, build_le_nif(VERTS, NORMALS, UVS, TRI))
    manifest = str(tmp_path / "work.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "items": [
            {"in": nif_a, "out": "a.gltf"},
            {"in": str(tmp_path / "missing.nif"), "out": "b.gltf"},
        ]}, fh)
    assert main(["--manifest", manifest, "--outdir", str(tmp_path / "out")]) == 1

"""Offline glTF MASK byte contracts through the package CLI, NIF, and DDS.

These checks compare the documented glTF cutoff rule with the nif.xml bitfield;
they do not claim Skyrim/GPU runtime validation.
"""

from __future__ import annotations

import base64
import json
from math import ceil
from pathlib import Path
import struct
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from any2nif.package import MANIFEST, texture_references
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header
from tests.gltf2nif_fixtures import CUBE_POS, CUBE_TRIS, write_gltf_interleaved
from tests.tex2dds_fixtures import png_bytes


# niftools/nif.xml AlphaFlags and TestFunction, repeated here rather than imported
# from the writer so this test remains an independent format comparison.
_ALPHA_BLEND_ENABLE = 1 << 0
_ALPHA_TEST_ENABLE = 1 << 9
_TEST_FUNCTION_SHIFT = 10
_TEST_FUNCTION_MASK = 0b111
_GREATER_EQUAL = 6
_NEVER = 7


def _block(data: bytes, block_type: str) -> int:
    header = _read_header(_Reader(data))
    offsets = [offset for offset, kind in zip(header["offsets"], header["types"])
               if kind == block_type]
    assert len(offsets) == 1
    return offsets[0]


def _alpha_property(data: bytes) -> tuple[int, int]:
    offset = _block(data, "NiAlphaProperty")
    return struct.unpack_from("<H", data, offset + 12)[0], data[offset + 14]


def _lighting_alpha(data: bytes) -> float:
    return struct.unpack_from("<f", data, _block(data, "BSLightingShaderProperty") + 64)[0]


def _mask_keeps(flags: int, threshold: int, alpha: int) -> bool:
    assert flags & _ALPHA_TEST_ENABLE
    function = (flags >> _TEST_FUNCTION_SHIFT) & _TEST_FUNCTION_MASK
    assert function == _GREATER_EQUAL
    return alpha >= threshold


def _source(tmp_path: Path, cutoff: float, *, factor_alpha: float = 1.0) -> tuple[Path, list[int]]:
    threshold = ceil(cutoff * 255)
    alphas = sorted({0, max(0, threshold - 1), threshold,
                     min(255, threshold + 1), 255})
    rgba = np.empty((8, 8, 4), dtype=np.uint8)
    rgba[:, :, :3] = (180, 90, 30)
    rgba[:, :, 3] = np.asarray([alphas[x * len(alphas) // 8] for x in range(8)])

    source = tmp_path / "masked.gltf"
    write_gltf_interleaved(source.as_posix(), [{
        "positions": CUBE_POS,
        "normals": [(0, 1, 0)] * 8,
        "uvs": [(0, 0)] * 8,
        "triangles": CUBE_TRIS,
        "material": "masked",
    }])
    document = json.loads(source.read_text())
    payload = base64.b64encode(png_bytes(rgba)).decode("ascii")
    document["images"] = [{"uri": f"data:image/png;base64,{payload}"}]
    document["textures"] = [{"source": 0}]
    document["materials"][0].update({"alphaMode": "MASK", "alphaCutoff": cutoff})
    document["materials"][0]["pbrMetallicRoughness"] = {
        "baseColorTexture": {"index": 0},
        "baseColorFactor": [0.5, 0.75, 1.0, factor_alpha],
    }
    source.write_text(json.dumps(document))
    return source, rgba[0, :, 3].tolist()


@pytest.mark.parametrize("cutoff", [0.0, 0.25, 0.499, 0.5, 1.0])
def test_package_mask_matches_gltf_cutoff_at_quantized_boundaries(tmp_path, cutoff):
    source, source_alpha = _source(tmp_path, cutoff)
    output = tmp_path / "Data"
    result = subprocess.run(
        [sys.executable, "-m", "any2nif", str(source), str(output), "--package",
         "--collision", "none"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    flags, threshold = _alpha_property(nif)
    assert flags & _ALPHA_BLEND_ENABLE == 0
    assert threshold == ceil(cutoff * 255)
    assert _lighting_alpha(nif) == pytest.approx(1.0)

    diffuse_ref = next(ref for ref in texture_references(nif)
                       if not ref.endswith(("_n.dds", "_s.dds", "_g.dds")))
    with Image.open(output / diffuse_ref.replace("\\", "/")) as image:
        dds_alpha = np.asarray(image.convert("RGBA"), dtype=np.uint8)[0, :, 3].tolist()

    # BC3 may move a non-endpoint alpha by one code value, but it must preserve
    # every cutoff classification represented by these boundary bands.
    np.testing.assert_allclose(dds_alpha, source_alpha, atol=1)
    expected = [alpha / 255.0 >= cutoff for alpha in source_alpha]
    assert [_mask_keeps(flags, threshold, alpha) for alpha in dds_alpha] == expected


def test_package_stores_base_color_factor_alpha_without_baking_it_twice(tmp_path):
    source, source_alpha = _source(tmp_path, 0.5, factor_alpha=0.4)
    output = tmp_path / "Data"
    result = subprocess.run(
        [sys.executable, "-m", "any2nif", str(source), str(output), "--package",
         "--collision", "none"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    assert _lighting_alpha(nif) == pytest.approx(0.4)
    diffuse_ref = next(ref for ref in texture_references(nif)
                       if not ref.endswith(("_n.dds", "_s.dds", "_g.dds")))
    with Image.open(output / diffuse_ref.replace("\\", "/")) as image:
        dds_alpha = np.asarray(image.convert("RGBA"), dtype=np.uint8)[0, :, 3].tolist()
    # A 0.4 bake would be far outside BC3's one-code-value approximation.
    np.testing.assert_allclose(dds_alpha, source_alpha, atol=1)


def test_package_mask_cutoff_above_one_encodes_test_never(tmp_path):
    source, _ = _source(tmp_path, 1.0)
    document = json.loads(source.read_text())
    document["materials"][0]["alphaCutoff"] = 1.1
    source.write_text(json.dumps(document))
    output = tmp_path / "Data"
    result = subprocess.run(
        [sys.executable, "-m", "any2nif", str(source), str(output), "--package",
         "--collision", "none"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    flags, threshold = _alpha_property(nif)
    assert flags & _ALPHA_BLEND_ENABLE == 0
    assert flags & _ALPHA_TEST_ENABLE
    assert (flags >> _TEST_FUNCTION_SHIFT) & _TEST_FUNCTION_MASK == _NEVER
    assert threshold == 255

"""any2nif front-door contract: dispatch, unit/axis transform, exit codes.

Format-specific coverage lives in tests/test_any2nif_trimesh.py and
tests/test_any2nif_fbx.py; this file owns the CLI surface and the glTF/GLB
native path (which needs no conversion at all -- gltf2nif already reads both).
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from any2nif import AnyError, normalize_to_gltf  # noqa: E402
from any2nif import transform  # noqa: E402
from any2nif.cli import main  # noqa: E402
from gltf2nif import read_gltf  # noqa: E402
from gltf2nif.geometry import Mesh  # noqa: E402
from nif2gltf.nif_reader import read_nif  # noqa: E402


def _write_triangle_gltf(directory: Path, *, glb: bool = False, name: str = "tri") -> Path:
    """A single asymmetric triangle with UVs, as .gltf+.bin or self-contained .glb."""
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 2.0, 0.5)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    pos_blob = b"".join(struct.pack("<3f", *p) for p in positions)
    uv_blob = b"".join(struct.pack("<2f", *t) for t in uvs)
    idx_blob = struct.pack("<3H", 0, 1, 2)
    blob = pos_blob + uv_blob + idx_blob
    while len(blob) % 4:
        blob += b"\0"
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
            "indices": 2, "material": 0,
        }]}],
        "materials": [{"name": "rock01.png"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0.0, 0.0, 0.0], "max": [1.0, 2.0, 0.5]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_blob)},
            {"buffer": 0, "byteOffset": len(pos_blob), "byteLength": len(uv_blob)},
            {"buffer": 0, "byteOffset": len(pos_blob) + len(uv_blob), "byteLength": len(idx_blob)},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }
    if glb:
        doc["buffers"][0].pop("uri", None)
        json_bytes = json.dumps(doc, separators=(",", ":")).encode()
        while len(json_bytes) % 4:
            json_bytes += b" "
        chunks = (struct.pack("<I", len(json_bytes)) + b"JSON" + json_bytes +
                  struct.pack("<I", len(blob)) + b"BIN\0" + blob)
        out = directory / f"{name}.glb"
        out.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks)
        return out
    doc["buffers"][0]["uri"] = f"{name}.bin"
    (directory / f"{name}.bin").write_bytes(blob)
    out = directory / f"{name}.gltf"
    out.write_text(json.dumps(doc))
    return out


class NormalizeDispatchTests(unittest.TestCase):
    def test_native_gltf_and_glb_pass_through_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for glb in (False, True):
                src = _write_triangle_gltf(tmp, glb=glb, name="glb" if glb else "json")
                self.assertEqual(normalize_to_gltf(str(src), str(tmp)), str(src))

    def test_missing_source_is_general_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AnyError) as ctx:
                normalize_to_gltf(os.path.join(tmp, "nope.obj"), tmp)
            self.assertEqual(ctx.exception.code, 1)

    def test_unknown_extension_is_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "thing.blend"
            src.write_bytes(b"not a model")
            with self.assertRaises(AnyError) as ctx:
                normalize_to_gltf(str(src), tmp)
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("unsupported source extension", str(ctx.exception))


class TransformTests(unittest.TestCase):
    def _mesh(self):
        return Mesh(name="m", positions=[(1.0, 2.0, 3.0)], normals=[(0.0, 1.0, 0.0)],
                    uvs=[(0.0, 0.0)], triangles=[(0, 0, 0)])

    def test_y_up_metres_is_a_no_op(self):
        mesh = self._mesh()
        transform.apply([mesh], scale=1.0, up_axis="y")
        self.assertEqual(mesh.positions, [(1.0, 2.0, 3.0)])
        self.assertEqual(mesh.normals, [(0.0, 1.0, 0.0)])

    def test_z_up_source_rotates_into_gltf_convention(self):
        mesh = self._mesh()
        transform.apply([mesh], scale=1.0, up_axis="z")
        self.assertEqual(mesh.positions, [(1.0, 3.0, -2.0)])
        self.assertEqual(mesh.normals, [(0.0, 0.0, -1.0)])

    def test_scale_touches_positions_not_normals(self):
        mesh = self._mesh()
        transform.apply([mesh], scale=0.01, up_axis="y")
        np.testing.assert_allclose(mesh.positions, [(0.01, 0.02, 0.03)])
        self.assertEqual(mesh.normals, [(0.0, 1.0, 0.0)])

    def test_unit_and_scale_multiply(self):
        self.assertAlmostEqual(transform.resolve_scale("cm", 2.0), 0.02)
        self.assertAlmostEqual(transform.resolve_scale(None, None), 1.0)
        self.assertAlmostEqual(transform.resolve_scale("in", None), 0.0254)

    def test_unknown_unit_and_axis_are_errors(self):
        with self.assertRaises(AnyError):
            transform.resolve_scale("furlong", None)
        with self.assertRaises(AnyError):
            transform.apply([self._mesh()], up_axis="w")


class CliTests(unittest.TestCase):
    def test_glb_round_trips_through_any2nif_back_into_nif2gltf(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp, glb=True)
            out = tmp / "out.nif"
            self.assertEqual(main([str(src), str(out)]), 0)
            self.assertTrue(out.is_file() and out.stat().st_size > 0)
            back = read_nif(out.read_bytes())
            self.assertEqual(sum(len(m.triangles) for m in back), 1)

    def test_material_basename_reaches_the_nif_texture_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp)
            out = tmp / "out.nif"
            self.assertEqual(main([str(src), str(out), "--texprefix", "textures\\demo"]), 0)
            self.assertIn(b"textures\\demo\\rock01.dds", out.read_bytes())

    def test_unit_scaling_changes_the_written_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp)
            metres, centimetres = tmp / "m.nif", tmp / "cm.nif"
            self.assertEqual(main([str(src), str(metres)]), 0)
            self.assertEqual(main([str(src), str(centimetres), "--unit", "cm"]), 0)
            big = max(abs(c) for m in read_nif(metres.read_bytes()) for p in m.positions for c in p)
            small = max(abs(c) for m in read_nif(centimetres.read_bytes()) for p in m.positions for c in p)
            self.assertAlmostEqual(small, big * 0.01, places=3)

    def test_up_axis_z_reorients_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp)
            yup, zup = tmp / "y.nif", tmp / "z.nif"
            self.assertEqual(main([str(src), str(yup)]), 0)
            self.assertEqual(main([str(src), str(zup), "--up-axis", "z"]), 0)
            self.assertNotEqual(yup.read_bytes(), zup.read_bytes())

    def test_keep_intermediate_leaves_nothing_for_native_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp)
            keep = tmp / "keep"
            self.assertEqual(main([str(src), str(tmp / "o.nif"), "--keep-intermediate", str(keep)]), 0)
            self.assertTrue(keep.is_dir())  # created, and native input needs no copy
            self.assertEqual(list(keep.iterdir()), [])

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self.assertEqual(main([str(tmp / "missing.glb"), str(tmp / "o.nif")]), 1)
            bad = tmp / "bad.gltf"
            bad.write_text("{not json")
            self.assertEqual(main([str(bad), str(tmp / "o.nif")]), 2)
            unknown = tmp / "x.blend"
            unknown.write_bytes(b"\0\0")
            self.assertEqual(main([str(unknown), str(tmp / "o.nif")]), 2)

    def test_skinned_source_is_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp)
            doc = json.loads(src.read_text())
            doc["nodes"][0]["skin"] = 0
            doc["skins"] = [{"joints": [0]}]
            src.write_text(json.dumps(doc))
            self.assertEqual(main([str(src), str(tmp / "o.nif")]), 3)

    def test_runs_as_a_real_subprocess_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = _write_triangle_gltf(tmp, glb=True)
            out = tmp / "sub.nif"
            proc = subprocess.run([sys.executable, "-m", "any2nif", str(src), str(out)],
                                  cwd=str(ROOT), capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()

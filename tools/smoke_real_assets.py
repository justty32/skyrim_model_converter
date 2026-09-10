"""Opt-in downloaded real asset trial; run from the repository root.

Sources, licenses and expected limitations are documented in REAL-ASSETS.md.
Downloads and Data outputs stay in ignored backend/real-assets/.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from pygltflib import GLTF2

from any2nif.cli import main
from any2nif.package import MANIFEST
from any2nif.package_materials import _material_texture_infos
from gltf2nif import read_gltf
from nif2gltf.nif_reader import read_nif

REV = "90d7ede14c7e280af263824604b427a1ca02cb66"
ASSETS = {
    "Lantern": "a79458c4b02d695187a952f23a63b8bf278e7bc3d316a3c2a314f2d6974181f1",
    "Avocado": "ccc9c3ce56423720b09399c2351537207cd5a65f859f9e6e2f30922762f3abd4",
    "SheenChair": "f0af2a2b102d28d540236306ae19f8fb36842df76bd38cf76f063f9bd2853399",
}


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="fetch missing pinned assets")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "backend" / "real-assets"
    root.mkdir(parents=True, exist_ok=True)
    for name, digest in ASSETS.items():
        source = root / f"{name}.glb"
        if not source.exists() and args.download:
            url = (f"https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/"
                   f"{REV}/Models/{name}/glTF-Binary/{name}.glb")
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read()
            assert hashlib.sha256(payload).hexdigest() == digest, name
            source.write_bytes(payload)
        assert source.exists(), f"Missing {source}; rerun with --download"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, name

    for name in ["Lantern", "Avocado", "SheenChair", "LanternUV"]:
        source = root / f"{name}.glb"
        if name == "LanternUV":
            # Controlled derivative of real geometry/images, not an upstream asset.
            gltf = GLTF2().load(str(root / "Lantern.glb"))
            for mesh in gltf.meshes:
                for primitive in mesh.primitives:
                    primitive.attributes.TEXCOORD_1 = primitive.attributes.TEXCOORD_0
            for material in gltf.materials:
                for _, info in _material_texture_infos(material):
                    transform = {"texCoord": 1, "scale": [2, 2], "offset": [.25, .5]}
                    if isinstance(info, dict):
                        info.setdefault("extensions", {})["KHR_texture_transform"] = transform
                    else:
                        info.extensions = {**(info.extensions or {}), "KHR_texture_transform": transform}
            gltf.save_binary(str(source))
        output = root / f"Data-{name}"
        code = main([str(source), str(output), "--package", "--collision", "convex-mesh"])
        if name == "SheenChair":
            assert code == 2 and not output.exists(), "mixed UV mapping must fail closed"
            print(f"{name}: expected rejection (mixed UV mappings)", flush=True)
            continue
        assert code == 0, name
        manifest = json.loads((output / MANIFEST).read_text())
        result = read_nif((output / manifest["mesh"]).read_bytes())
        original = read_gltf(str(root / ("Lantern.glb" if name == "LanternUV" else f"{name}.glb")))
        assert len(result) == len(original)
        for before, after in zip(original, result):
            assert before.triangles == after.triangles
            np.testing.assert_allclose(np.asarray(after.positions) / 70.03, before.positions, atol=2e-5)
            expected_uv = np.asarray(before.uvs)
            if name == "LanternUV":
                expected_uv = expected_uv * 2 + [.25, .5]
            np.testing.assert_allclose(after.uvs, expected_uv, atol=.004)
        print(f"{name}: {len(result)} shapes, {sum(len(m.triangles) for m in result)} triangles, "
              f"{len(manifest['textures'])} DDS; geometry/UV readback passed", flush=True)


if __name__ == "__main__":
    run()

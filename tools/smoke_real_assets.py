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
from PIL import Image
from pygltflib import GLTF2

from any2nif.cli import main
from any2nif.package import MANIFEST, texture_references
from any2nif.package_materials import (_buffers, _decode_rgba, _image_bytes,
                                       _material_texture_infos)
from any2nif.package_uv import _mapping
from any2nif.texture_sampling import sample_rgba
from gltf2nif import read_gltf
from gltf2nif.gltf_reader import _read_accessor, _scene_instances
from nif2gltf.nif_reader import read_nif

REV = "90d7ede14c7e280af263824604b427a1ca02cb66"
ASSETS = {
    "Lantern": "a79458c4b02d695187a952f23a63b8bf278e7bc3d316a3c2a314f2d6974181f1",
    "Avocado": "ccc9c3ce56423720b09399c2351537207cd5a65f859f9e6e2f30922762f3abd4",
    "SheenChair": "f0af2a2b102d28d540236306ae19f8fb36842df76bd38cf76f063f9bd2853399",
}


def _corner_geometry(before, after, *, atlas=False):
    """Compare indexed corners because an atlas is allowed to duplicate vertices."""
    assert len(before.triangles) == len(after.triangles)
    before_triangles = np.asarray(before.triangles)
    after_triangles = np.asarray(after.triangles)
    np.testing.assert_allclose(
        np.asarray(after.positions)[after_triangles] / 70.03,
        np.asarray(before.positions)[before_triangles], atol=2e-5)
    np.testing.assert_allclose(
        np.asarray(after.normals)[after_triangles],
        np.asarray(before.normals)[before_triangles], atol=1.5 / 255)
    if atlas:
        uv = np.asarray(after.uvs)
        assert uv.shape == (len(after.positions), 2)
        assert np.isfinite(uv).all() and np.all((uv >= 0) & (uv <= 1))


def _srgb_encode(linear):
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= .0031308, linear * 12.92,
                    1.055 * linear ** (1 / 2.4) - .055)


def _source_image(gltf, buffers, payloads, info):
    texture = gltf.textures[info.index]
    image = _decode_rgba(payloads[texture.source][0], texture.source)
    sampler = gltf.samplers[texture.sampler] if texture.sampler is not None else None
    return image, dict(wrap_s=getattr(sampler, 'wrapS', None) or 10497,
                       wrap_t=getattr(sampler, 'wrapT', None) or 10497,
                       linear=getattr(sampler, 'magFilter', None) != 9728)


def _mapped_uv(gltf, buffers, primitive, info, slot):
    coord, offset, scale, rotation = _mapping(info, slot)
    uv = np.asarray(_read_accessor(gltf, buffers,
                    getattr(primitive.attributes, f'TEXCOORD_{coord}')), dtype=np.float64)
    c, s = np.cos(rotation), np.sin(rotation)
    return (uv * scale) @ np.array([[c, s], [-s, c]]) + offset


def validate_sheen_diffuse(source, output, original, result):
    """Compare source glTF material evaluation with the packaged BC1 diffuse."""
    gltf = GLTF2().load(str(source))
    buffers = _buffers(gltf, str(source.parent))
    payloads = [_image_bytes(gltf, buffers, str(source.parent), i)
                for i in range(len(gltf.images))]
    primitives = [primitive for _, mesh_index, _ in _scene_instances(gltf)
                  for primitive in gltf.meshes[mesh_index].primitives]
    manifest = json.loads((output / MANIFEST).read_text())
    refs = texture_references((output / manifest['mesh']).read_bytes())
    errors_by_material = {}
    barycentrics = np.array([[1/3, 1/3, 1/3], [.6, .2, .2], [.2, .6, .2], [.2, .2, .6]])
    for primitive, before, after in zip(primitives, original, result):
        material_index = primitive.material
        material = gltf.materials[material_index]
        target_uv = np.asarray(after.uvs)
        source_triangles = np.asarray(before.triangles)
        target_triangles = np.asarray(after.triangles)
        base_info = material.pbrMetallicRoughness.baseColorTexture
        base_uv = (_mapped_uv(gltf, buffers, primitive, base_info, 'baseColorTexture')
                   if base_info is not None else None)
        base_source = (_source_image(gltf, buffers, payloads, base_info)
                       if base_info is not None else None)
        ao_info = material.occlusionTexture
        ao_uv = _mapped_uv(gltf, buffers, primitive, ao_info, 'occlusionTexture')
        ao_image, ao_options = _source_image(gltf, buffers, payloads, ao_info)
        factor = np.asarray(material.pbrMetallicRoughness.baseColorFactor or [1, 1, 1, 1])
        diffuse_ref = next(ref for ref in refs
                           if ref.replace('\\', '/').endswith(f'material_{material_index:04d}.dds'))
        with Image.open(output / diffuse_ref.replace('\\', '/')) as image:
            dds = np.asarray(image.convert('RGBA'))
        errors = []
        for source_tri, target_tri in zip(source_triangles, target_triangles):
            atlas_corners = target_uv[target_tri]
            # Skip tiny charts: their BC1 blocks cannot represent a stable interior sample.
            height, width = dds.shape[:2]
            edge1 = (atlas_corners[1] - atlas_corners[0]) * [width, height]
            edge2 = (atlas_corners[2] - atlas_corners[0]) * [width, height]
            area_pixels = abs(edge1[0] * edge2[1] - edge1[1] * edge2[0]) / 2
            if area_pixels < 8:
                continue
            atlas_points = barycentrics @ atlas_corners
            actual = sample_rgba(dds, atlas_points, linear=True)[:, :3]
            if base_info is None:
                base = np.ones((len(barycentrics), 4))
            else:
                image, options = base_source
                base = sample_rgba(image, barycentrics @ base_uv[source_tri],
                                   srgb=True, **options)
            ao = sample_rgba(ao_image, barycentrics @ ao_uv[source_tri],
                             **ao_options)[:, 0]
            strength = 1 if ao_info.strength is None else ao_info.strength
            expected = _srgb_encode(base[:, :3] * factor[:3] *
                                    (1 + strength * (ao[:, None] - 1)))
            errors.extend(np.abs(actual - expected).reshape(-1))
        errors = np.asarray(errors)
        assert len(errors) >= 24, f'material {material_index}: too few stable atlas samples'
        mae, p90 = float(errors.mean()), float(np.quantile(errors, .9))
        assert mae < .025 and p90 < .06, (material_index, mae, p90, len(errors) // 3)
        errors_by_material[material_index] = (mae, p90, len(errors) // 3)
    assert set(errors_by_material) == {primitive.material for primitive in primitives}
    return errors_by_material


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
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        code = main([str(source), str(output), "--package", "--collision", "convex-mesh"])
        assert code == 0, name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest, name
        manifest = json.loads((output / MANIFEST).read_text())
        result = read_nif((output / manifest["mesh"]).read_bytes())
        original = read_gltf(str(root / ("Lantern.glb" if name == "LanternUV" else f"{name}.glb")))
        assert len(result) == len(original)
        for before, after in zip(original, result):
            _corner_geometry(before, after, atlas=name == "SheenChair")
            if name != "SheenChair":
                expected_uv = np.asarray(before.uvs)
                if name == "LanternUV":
                    expected_uv = expected_uv * 2 + [.25, .5]
                np.testing.assert_allclose(after.uvs, expected_uv, atol=.004)
        if name == "SheenChair":
            stats = validate_sheen_diffuse(source, output, original, result)
            print('SheenChair diffuse samples: ' + ', '.join(
                f'material {i}: n={n}, MAE={mae:.4f}, p90={p90:.4f}'
                for i, (mae, p90, n) in sorted(stats.items())), flush=True)
        print(f"{name}: {len(result)} shapes, {sum(len(m.triangles) for m in result)} triangles, "
              f"{len(manifest['textures'])} DDS; geometry/atlas readback passed", flush=True)


if __name__ == "__main__":
    run()

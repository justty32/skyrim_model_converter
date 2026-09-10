"""Opposite UV handedness across a shared edge must form a tangent seam."""

import json

import numpy as np
import pytest
from PIL import Image

from any2nif.cli import main
from any2nif.package import MANIFEST
from any2nif.texture_sampling import sample_rgba
from any2nif.uv_atlas import rasterize
from nif2gltf.nif_reader import read_nif
from tests.test_generated_tangent_frames import _NORMAL_RGB, _source
from tests.test_package_normal_frames import _stored_nif_frames


POSITIONS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
TRIANGLES = np.array([[0, 1, 2], [0, 2, 3]])
# Face 0 determinant +1; face 1 determinant -1.  Shared vertices 0 and 2
# therefore require two independently smoothed tangent-frame groups.
FOLDED_UV = np.array([[0, 0], [1, 0], [1, 1], [2, 1]], float)


def _face_frame(face):
    a, b, c = TRIANGLES[face]
    edge1, edge2 = POSITIONS[b] - POSITIONS[a], POSITIONS[c] - POSITIONS[a]
    duv1, duv2 = FOLDED_UV[b] - FOLDED_UV[a], FOLDED_UV[c] - FOLDED_UV[a]
    determinant = duv1[0] * duv2[1] - duv2[0] * duv1[1]
    tangent = (duv2[1] * edge1 - duv1[1] * edge2) / determinant
    tangent /= np.linalg.norm(tangent)
    bitangent_direction = (-duv2[0] * edge1 + duv1[0] * edge2) / determinant
    normal = np.array((0, 0, 1), float)
    handedness = np.sign(np.dot(np.cross(normal, tangent), bitangent_direction))
    return np.column_stack((tangent, np.cross(normal, tangent) * handedness, normal))


@pytest.mark.parametrize("force_atlas", [False, True], ids=["direct", "white-ao-atlas"])
def test_opposite_uv_handedness_is_split_into_nonsingular_face_frames(tmp_path, force_atlas):
    source = tmp_path / "folded.gltf"
    _source(source, FOLDED_UV, force_atlas=force_atlas)
    source_before = source.read_bytes()
    output = tmp_path / "Data"
    assert main([str(source), str(output), "--package", "--bake-size", "64",
                 "--collision", "none"]) == 0
    assert source.read_bytes() == source_before

    manifest = json.loads((output / MANIFEST).read_text())
    nif = (output / manifest["mesh"]).read_bytes()
    mesh = read_nif(nif)[0]
    triangles = np.asarray(mesh.triangles, int)
    positions = np.asarray(mesh.positions, float) / 70.03
    frames = _stored_nif_frames(nif)
    assert len(triangles) == 2
    np.testing.assert_allclose(positions[triangles], POSITIONS[TRIANGLES], atol=2e-5)

    normal_ref = next(ref for ref in manifest["textures"] if ref.endswith("_n.dds"))
    with Image.open(output / normal_ref.replace("\\", "/")) as image:
        rgba = np.asarray(image.convert("RGBA"))
    encoded = _NORMAL_RGB / 255 * 2 - 1
    encoded /= np.linalg.norm(encoded)
    owners = barycentric = None
    if force_atlas:
        owners, barycentric = rasterize(np.asarray(mesh.uvs), triangles, 64)
    for face, triangle in enumerate(triangles):
        corner_frames = frames[triangle]
        assert np.all(np.abs(np.linalg.det(corner_frames)) > .95)
        # The edge midpoint was the exact singular location before seam splitting.
        midpoint = corner_frames[[0, 2]].mean(axis=0)
        midpoint /= np.linalg.norm(midpoint, axis=0)
        assert abs(np.linalg.det(midpoint)) > .95
        if force_atlas:
            # Read an actual covered texel well inside this chart.  Sampling the
            # geometric centroid can bilinearly mix a neighbouring chart after
            # BC3 compression, which does not exercise the frame at that point.
            ys, xs = np.nonzero(owners == face)
            weights = barycentric[ys, xs]
            interior = np.argmax(weights.min(axis=1))
            weights = weights[interior]
            sampled = rgba[ys[interior], xs[interior], :3] / 255
        else:
            weights = np.full(3, 1 / 3)
            uv = np.asarray(mesh.uvs)[triangle].mean(axis=0)
            sampled = sample_rgba(rgba, uv[None])[0, :3]
        sampled[1] = 1 - sampled[1]
        tangent_normal = sampled * 2 - 1
        tangent_normal /= np.linalg.norm(tangent_normal)
        sample_frame = np.einsum("i,ijk->jk", weights, corner_frames)
        sample_frame /= np.linalg.norm(sample_frame, axis=0)
        actual = sample_frame @ tangent_normal
        actual /= np.linalg.norm(actual)
        expected = _face_frame(face) @ encoded
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(actual, expected, atol=.12)

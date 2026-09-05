"""Synthetic images and glTF/GLB scenes for the tex2dds / export_textures tests.

Everything is generated at run time -- no binary asset enters the repo. The
readback helpers deliberately go through **Pillow's own DDS decoder**, so the
assertions cross-check our encoder against a third-party implementation rather
than against itself.
"""

from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image
from pygltflib import (
    GLTF2,
    Asset,
    Buffer,
    BufferView,
    Image as GltfImage,
    Material,
    NormalMaterialTexture,
    PbrMetallicRoughness,
    Texture,
    TextureInfo,
)


# --------------------------------------------------------------------------- images

def flat_rgb(size: int, colour: tuple[int, int, int]) -> np.ndarray:
    """A solid colour block, the case BC1 must reproduce almost exactly."""
    out = np.zeros((size, size, 3), dtype=np.uint8)
    out[:] = colour
    return out


def gradient_rgb(width: int = 64, height: int = 64) -> np.ndarray:
    """Smooth ramps in all three channels -- BC1's best case for a real image."""
    yy, xx = np.mgrid[0:height, 0:width]
    r = (xx * 255 // max(1, width - 1)).astype(np.uint8)
    g = (yy * 255 // max(1, height - 1)).astype(np.uint8)
    b = ((xx + yy) * 255 // max(1, width + height - 2)).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


def gradient_rgba(width: int = 64, height: int = 64) -> np.ndarray:
    """Gradient with a real (non-opaque) alpha ramp -> `auto` must pick BC3."""
    rgb = gradient_rgb(width, height)
    yy, xx = np.mgrid[0:height, 0:width]
    alpha = (xx * 255 // max(1, width - 1)).astype(np.uint8)
    return np.concatenate([rgb, alpha[:, :, None]], axis=-1)


def opaque_rgba(width: int = 32, height: int = 32) -> np.ndarray:
    """RGBA whose alpha is uniformly 255 -> `auto` must still pick BC1."""
    rgb = gradient_rgb(width, height)
    alpha = np.full((height, width, 1), 255, dtype=np.uint8)
    return np.concatenate([rgb, alpha], axis=-1)


def png_bytes(array: np.ndarray) -> bytes:
    mode = "RGBA" if array.shape[2] == 4 else "RGB"
    buffer = io.BytesIO()
    Image.fromarray(array, mode).save(buffer, format="PNG")
    return buffer.getvalue()


def write_png(path: str, array: np.ndarray) -> str:
    mode = "RGBA" if array.shape[2] == 4 else "RGB"
    Image.fromarray(array, mode).save(path, format="PNG")
    return path


# ------------------------------------------------------------------------ dds readback

def decode_dds(source) -> np.ndarray:
    """Read a .dds (path or bytes) back with Pillow -> (H, W, 4) int16 RGBA.

    int16 so test arithmetic can subtract without wrapping around.
    """
    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with Image.open(handle) as img:
        return np.asarray(img.convert("RGBA"), dtype=np.int16)


def rmse(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(((a.astype(np.float64) - b.astype(np.float64)) ** 2).mean()))


# ------------------------------------------------------------------------- glTF scenes

def _append_image(blob: bytearray, views: list[BufferView], images: list[GltfImage],
                  array: np.ndarray) -> int:
    """Embed one PNG in the GLB blob, 4-byte aligned, and return its image index."""
    payload = png_bytes(array)
    offset = len(blob)
    blob.extend(payload)
    blob.extend(b"\0" * ((-len(payload)) % 4))
    views.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(payload)))
    images.append(GltfImage(bufferView=len(views) - 1, mimeType="image/png"))
    return len(images) - 1


def build_glb(path: str, materials: list[dict]) -> str:
    """Write a .glb whose materials carry embedded PNG textures.

    Each entry of `materials` is a dict with a "name" plus any of the optional
    keys "diffuse", "normal", "metallicRoughness", "specular" (arrays), where
    "specular" goes through the KHR_materials_specular extension.
    """
    blob = bytearray()
    views: list[BufferView] = []
    images: list[GltfImage] = []
    textures: list[Texture] = []
    out_materials: list[Material] = []

    def texture_for(array) -> int:
        textures.append(Texture(source=_append_image(blob, views, images, array)))
        return len(textures) - 1

    for spec in materials:
        pbr = PbrMetallicRoughness()
        material = Material(name=spec.get("name"), pbrMetallicRoughness=pbr)
        if spec.get("diffuse") is not None:
            pbr.baseColorTexture = TextureInfo(index=texture_for(spec["diffuse"]))
        if spec.get("metallicRoughness") is not None:
            pbr.metallicRoughnessTexture = TextureInfo(
                index=texture_for(spec["metallicRoughness"]))
        if spec.get("normal") is not None:
            material.normalTexture = NormalMaterialTexture(index=texture_for(spec["normal"]))
        if spec.get("specular") is not None:
            material.extensions = {
                "KHR_materials_specular": {
                    "specularTexture": {"index": texture_for(spec["specular"])},
                }
            }
        out_materials.append(material)

    gltf = GLTF2(
        asset=Asset(version="2.0"),
        buffers=[Buffer(byteLength=len(blob))],
        bufferViews=views,
        images=images,
        textures=textures,
        materials=out_materials,
    )
    gltf.set_binary_blob(bytes(blob))
    gltf.save_binary(path)
    return path


def build_gltf_with_external_images(directory: str, name: str, diffuse: np.ndarray) -> str:
    """A .gltf whose single material points at a sibling .png file on disk."""
    write_png(os.path.join(directory, "diffuse.png"), diffuse)
    gltf = GLTF2(
        asset=Asset(version="2.0"),
        buffers=[],
        images=[GltfImage(uri="diffuse.png")],
        textures=[Texture(source=0)],
        materials=[Material(name=name,
                            pbrMetallicRoughness=PbrMetallicRoughness(
                                baseColorTexture=TextureInfo(index=0)))],
    )
    path = os.path.join(directory, "scene.gltf")
    gltf.save_json(path)
    return path


def build_gltf_with_data_uri(directory: str, name: str, diffuse: np.ndarray) -> str:
    """A .gltf whose image is an inline base64 `data:` URI."""
    import base64

    encoded = base64.b64encode(png_bytes(diffuse)).decode("ascii")
    gltf = GLTF2(
        asset=Asset(version="2.0"),
        buffers=[],
        images=[GltfImage(uri=f"data:image/png;base64,{encoded}")],
        textures=[Texture(source=0)],
        materials=[Material(name=name,
                            pbrMetallicRoughness=PbrMetallicRoughness(
                                baseColorTexture=TextureInfo(index=0)))],
    )
    path = os.path.join(directory, "embedded.gltf")
    gltf.save_json(path)
    return path

r"""glTF material images -> Skyrim .dds files.

Fills the gap AUDIT.md calls out: `gltf2nif` only ever used a material's *name*
as a texture base name and probed the filesystem for a sibling `<base>_n.dds`,
so any image actually carried by the glTF was dropped. This module decodes those
images and writes the three Skyrim slots next to the .nif, using `tex2dds` (pure
Python BC1/BC3, no external tool).

Slot mapping, and where each one is an approximation:

* `pbrMetallicRoughness.baseColorTexture` -> `<base>.dds`, BC1 or BC3 by alpha.
* `normalTexture` -> `<base>_n.dds`. Skyrim normal maps are DirectX-convention
  (green = Y down) while glTF is OpenGL-convention (Y up), so green is inverted;
  BC3 is forced because the alpha channel of a Skyrim `_n` carries glossiness.
* `pbrMetallicRoughness.metallicRoughnessTexture` -> `<base>_s.dds`, BC1.
  **Approximation.** glTF stores *roughness* in the green channel, while a
  Skyrim `_s` map is a *specular* mask -- the rough inverse of roughness. We
  therefore write `255 - G` replicated to grey. This is a visual approximation,
  not a physically correct conversion: glTF has no specular-mask channel and
  Skyrim's shader has no roughness input, so the two cannot round-trip exactly.
  When `KHR_materials_specular` is present it is preferred, since it carries a
  real specular signal (`specularTexture` alpha, else `specularColorTexture`
  luminance).

The material base name is derived exactly the way `gltf2nif.gltf_reader`
does it (`os.path.splitext(material.name or "")[0]`) -- if the two ever
disagreed, the written files would not line up with the texture paths the NIF
records. Materials with an empty base name are skipped.
"""

from __future__ import annotations

import base64
import io
import os
from urllib.parse import unquote

import numpy as np
from PIL import Image
from pygltflib import GLTF2

from tex2dds import encode_dds
from tex2dds.pipeline import FORMATS

_SPECULAR_EXT = "KHR_materials_specular"


def _material_basename(material) -> str:
    """Texture base name of a glTF material.

    Mirrors `gltf2nif.gltf_reader._material_basename`: the extractor records the
    texture base name as the material name, sometimes with a source extension
    (.tga/.dds/.png), which is stripped.
    """
    return os.path.splitext(getattr(material, "name", None) or "")[0]


def _buffer_bytes(gltf: GLTF2, gltf_dir: str) -> list[bytes]:
    """Raw bytes of every glTF buffer: GLB blob, `data:` payload, or sibling file."""
    out: list[bytes] = []
    for buf in gltf.buffers or []:
        uri = buf.uri
        if uri is None:  # .glb embedded blob
            out.append(gltf.binary_blob() or b"")
        elif uri.startswith("data:"):
            out.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            with open(os.path.join(gltf_dir, unquote(uri)), "rb") as fh:
                out.append(fh.read())
    return out


def _image_bytes(gltf: GLTF2, buffers: list[bytes], gltf_dir: str, index: int) -> bytes | None:
    """Encoded bytes of glTF image `index`, from a bufferView, `data:` URI, or file."""
    images = gltf.images or []
    if index is None or index < 0 or index >= len(images):
        return None
    image = images[index]
    if image.bufferView is not None:
        views = gltf.bufferViews or []
        if image.bufferView < 0 or image.bufferView >= len(views):
            return None
        view = views[image.bufferView]
        if view.buffer < 0 or view.buffer >= len(buffers):
            return None
        start = view.byteOffset or 0
        return buffers[view.buffer][start:start + view.byteLength]
    uri = image.uri
    if not uri:
        return None
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    path = os.path.join(gltf_dir, unquote(uri))
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def _texture_image(gltf: GLTF2, buffers: list[bytes], gltf_dir: str,
                   texture_index) -> np.ndarray | None:
    """Decode the image behind a glTF texture index into an (H, W, 4) uint8 array."""
    textures = gltf.textures or []
    if texture_index is None or texture_index < 0 or texture_index >= len(textures):
        return None
    raw = _image_bytes(gltf, buffers, gltf_dir, textures[texture_index].source)
    if not raw:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            return np.asarray(img.convert("RGBA"), dtype=np.uint8)
    except Exception:  # noqa: BLE001 - an undecodable image just means "no such slot"
        return None


def _tex_index(holder, attr: str):
    """`material.<attr>.index`, tolerating the many Nones pygltflib leaves around."""
    info = getattr(holder, attr, None) if holder is not None else None
    return getattr(info, "index", None) if info is not None else None


def _specular_source(gltf, material, buffers, gltf_dir) -> np.ndarray | None:
    """Grey specular mask for the `_s` slot, replicated across RGB. See module docstring."""
    ext = (getattr(material, "extensions", None) or {}).get(_SPECULAR_EXT) or {}
    if isinstance(ext, dict):
        for key, channel in (("specularTexture", 3), ("specularColorTexture", None)):
            info = ext.get(key)
            index = info.get("index") if isinstance(info, dict) else None
            rgba = _texture_image(gltf, buffers, gltf_dir, index)
            if rgba is None:
                continue
            if channel is not None:
                grey = rgba[:, :, channel]
            else:  # luminance of the specular colour
                grey = (rgba[:, :, :3].astype(np.float32) @ [0.2126, 0.7152, 0.0722])
                grey = np.clip(np.rint(grey), 0, 255).astype(np.uint8)
            return np.repeat(grey[:, :, None], 3, axis=2)

    pbr = getattr(material, "pbrMetallicRoughness", None)
    rgba = _texture_image(gltf, buffers, gltf_dir, _tex_index(pbr, "metallicRoughnessTexture"))
    if rgba is None:
        return None
    # glTF roughness lives in G; Skyrim _s wants specular -> approximate as its inverse.
    grey = 255 - rgba[:, :, 1]
    return np.repeat(grey[:, :, None], 3, axis=2)


def export_textures(gltf_path: str, out_dir: str, *, codec: str = "auto") -> dict[str, dict[str, str]]:
    """material basename -> {"diffuse": file, "normal": file, "specular": file}.

    Writes each material's images into `out_dir` as Skyrim-ready .dds (full mip
    chain, power-of-two). Returned paths are **file names relative to out_dir**,
    not absolute paths, so a caller can join them onto its own texture prefix.
    Slots absent from the material are absent from its dict; materials with an
    empty base name, or with no usable image at all, do not appear.

    `codec` is "auto" / "bc1" / "bc3" and applies to the diffuse slot; the normal
    slot is always BC3 (it needs the gloss alpha) and the specular slot BC1.
    """
    if codec not in FORMATS:
        raise ValueError(f"unknown codec {codec!r}; expected one of {', '.join(FORMATS)}")

    gltf = GLTF2().load(gltf_path)
    if gltf is None:
        return {}
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
    buffers = _buffer_bytes(gltf, gltf_dir)

    result: dict[str, dict[str, str]] = {}
    for material in gltf.materials or []:
        base = _material_basename(material)
        if not base:
            continue
        pbr = getattr(material, "pbrMetallicRoughness", None)
        slots: dict[str, str] = {}

        jobs = (
            ("diffuse", f"{base}.dds",
             _texture_image(gltf, buffers, gltf_dir, _tex_index(pbr, "baseColorTexture")),
             {"fmt": codec}),
            ("normal", f"{base}_n.dds",
             _texture_image(gltf, buffers, gltf_dir, _tex_index(material, "normalTexture")),
             {"fmt": "bc3", "normal_map": True}),
            ("specular", f"{base}_s.dds",
             _specular_source(gltf, material, buffers, gltf_dir),
             {"fmt": "bc1"}),
        )
        for slot, filename, pixels, options in jobs:
            if pixels is None:
                continue
            target = os.path.join(out_dir, filename)
            parent = os.path.dirname(os.path.abspath(target))
            os.makedirs(parent, exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(encode_dds(pixels, **options))
            slots[slot] = filename

        if slots:
            result[base] = slots
    return result

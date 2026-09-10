"""Prepare a self-contained glTF for the any2nif package pipeline."""

from __future__ import annotations

import base64
import binascii
import copy
import io
import os
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes

import numpy as np
from PIL import Image as PILImage
from pygltflib import GLTF2, Image, Material, PbrMetallicRoughness, Texture, TextureInfo

from .errors import AnyError


def _data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _decode_data_uri(uri: str, what: str) -> bytes:
    try:
        header, payload = uri.split(",", 1)
        if ";base64" in header:
            return base64.b64decode(payload, validate=True)
        return unquote_to_bytes(payload)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise AnyError(f"cannot decode {what} data URI: {exc}", code=2) from exc


def _read_uri(uri: str, source_dir: str, what: str) -> bytes:
    if uri.startswith("data:"):
        return _decode_data_uri(uri, what)
    path = os.path.join(source_dir, unquote(uri))
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise AnyError(f"cannot read {what} '{uri}': {exc}", code=1) from exc


def _buffers(gltf: GLTF2, source_dir: str) -> list[bytes]:
    result = []
    blob = gltf.binary_blob()
    for index, buffer in enumerate(gltf.buffers or []):
        if buffer.uri is None:
            if blob is None:
                raise AnyError(f"buffer {index} has no URI or GLB payload", code=2)
            raw = blob
        else:
            raw = _read_uri(buffer.uri, source_dir, f"buffer {index}")
        if len(raw) < (buffer.byteLength or 0):
            raise AnyError(f"buffer {index} is truncated", code=2)
        result.append(raw)
    return result


def _image_bytes(gltf: GLTF2, buffers: list[bytes], source_dir: str,
                 image_index: int) -> tuple[bytes, str]:
    if image_index < 0 or image_index >= len(gltf.images or []):
        raise AnyError(f"texture references missing image {image_index}", code=2)
    image = gltf.images[image_index]
    if image.bufferView is not None:
        views = gltf.bufferViews or []
        if image.bufferView < 0 or image.bufferView >= len(views):
            raise AnyError(f"image {image_index} references missing bufferView", code=2)
        view = views[image.bufferView]
        if view.buffer is None or view.buffer < 0 or view.buffer >= len(buffers):
            raise AnyError(f"image {image_index} references missing buffer", code=2)
        start = view.byteOffset or 0
        end = start + view.byteLength
        if start < 0 or end > len(buffers[view.buffer]):
            raise AnyError(f"image {image_index} exceeds its bufferView", code=2)
        raw = buffers[view.buffer][start:end]
    elif image.uri:
        raw = _read_uri(image.uri, source_dir, f"image {image_index}")
    else:
        raise AnyError(f"image {image_index} has neither URI nor bufferView", code=2)

    try:
        with PILImage.open(io.BytesIO(raw)) as decoded:
            decoded.load()
            detected = decoded.get_format_mimetype() or "image/png"
    except Exception as exc:  # Pillow exposes several decoder-specific exceptions.
        raise AnyError(f"cannot decode image {image_index}: {exc}", code=2) from exc
    return raw, image.mimeType or detected


def _texture_indices(value) -> set[int]:
    """Find texture-info dictionaries, including supported glTF material extensions."""
    found: set[int] = set()

    def visit(item, key: str = ""):
        if isinstance(item, dict):
            if key.lower().endswith("texture") and isinstance(item.get("index"), int):
                found.add(item["index"])
            for child_key, child in item.items():
                visit(child, child_key)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, key)
        elif hasattr(item, "to_dict"):
            visit(item.to_dict(), key)
        elif hasattr(item, "__dict__"):
            for child_key, child in vars(item).items():
                visit(child, child_key)

    visit(value)
    return found


def _used_texture_indices(gltf: GLTF2) -> set[int]:
    used: set[int] = set()
    for material in gltf.materials or []:
        used.update(_texture_indices(material))
    return used


def _material_texture_infos(material):
    """Yield (slot, texture-info) pairs from core material fields and extensions."""
    pbr = material.pbrMetallicRoughness
    for slot, info in (
        ("baseColorTexture", getattr(pbr, "baseColorTexture", None)),
        ("metallicRoughnessTexture", getattr(pbr, "metallicRoughnessTexture", None)),
        ("normalTexture", material.normalTexture),
        ("occlusionTexture", material.occlusionTexture),
        ("emissiveTexture", material.emissiveTexture),
    ):
        if info is not None:
            yield slot, info

    def visit(item, key=""):
        if isinstance(item, dict):
            if key.lower().endswith("texture") and isinstance(item.get("index"), int):
                yield key, item
            for child_key, child in item.items():
                yield from visit(child, child_key)
        elif isinstance(item, (list, tuple)):
            for child in item:
                yield from visit(child, key)

    yield from visit(material.extensions or {})


def _field(info, name, default=None):
    return info.get(name, default) if isinstance(info, dict) else getattr(info, name, default)


def _validate_texture_metadata(material, material_index: int) -> None:
    """Reject sampling metadata the current NIF/DDS pipeline would silently discard."""
    for slot, info in _material_texture_infos(material):
        if slot == "normalTexture":
            normal_scale = _field(info, "scale", 1.0)
            if normal_scale is not None and (
                isinstance(normal_scale, bool) or
                not isinstance(normal_scale, (int, float)) or
                not np.isfinite(normal_scale)
            ):
                raise AnyError(
                    f"material {material_index} normalTexture scale must be a finite number",
                    code=2,
                )


def _decode_rgba(raw: bytes, image_index: int) -> np.ndarray:
    try:
        with PILImage.open(io.BytesIO(raw)) as image:
            image.load()
            return np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except Exception as exc:
        raise AnyError(f"cannot decode image {image_index}: {exc}", code=2) from exc


def _bake_rgb(rgba: np.ndarray, factor: list[float]) -> bytes:
    baked = rgba.copy()
    for start in range(0, len(rgba), 64):
        rgb = rgba[start:start + 64, :, :3].astype(np.float64) / 255.0
        linear = np.where(rgb <= 0.04045, rgb / 12.92,
                          ((rgb + 0.055) / 1.055) ** 2.4)
        linear *= np.asarray(factor[:3], dtype=np.float64)[None, None, :]
        srgb = np.where(linear <= 0.0031308, linear * 12.92,
                        1.055 * np.power(linear, 1.0 / 2.4) - 0.055)
        baked[start:start + 64, :, :3] = np.clip(np.rint(srgb * 255.0), 0, 255).astype(np.uint8)
    output = io.BytesIO()
    PILImage.fromarray(baked, "RGBA").save(output, format="PNG")
    return output.getvalue()


def _ensure_diffuse(gltf: GLTF2, material, image_payloads: list[tuple[bytes, str]]) -> None:
    pbr = material.pbrMetallicRoughness
    if pbr is None:
        pbr = PbrMetallicRoughness()
        material.pbrMetallicRoughness = pbr
    factor = list(pbr.baseColorFactor or [1.0, 1.0, 1.0, 1.0])
    if (len(factor) != 4 or any(not np.isfinite(v) for v in factor) or
            any(v < 0.0 or v > 1.0 for v in factor)):
        raise AnyError("material has an invalid baseColorFactor", code=2)
    if pbr.baseColorTexture is None:
        rgba = np.full((4, 4, 4), 255, dtype=np.uint8)
        raw = _bake_rgb(rgba, factor)
        gltf.images.append(Image())
        image_index = len(gltf.images) - 1
        gltf.images[image_index].uri = _data_uri(raw, "image/png")
        gltf.images[image_index].mimeType = "image/png"
        gltf.textures.append(Texture(source=image_index))
        pbr.baseColorTexture = TextureInfo(index=len(gltf.textures) - 1)
        image_payloads.append((raw, "image/png"))
        return

    texture_index = pbr.baseColorTexture.index
    if texture_index is None or texture_index < 0 or texture_index >= len(gltf.textures):
        raise AnyError("baseColorTexture references a missing texture", code=2)
    image_index = gltf.textures[texture_index].source
    if image_index is None or image_index < 0 or image_index >= len(image_payloads):
        raise AnyError("baseColorTexture references a missing image", code=2)
    raw = _bake_rgb(_decode_rgba(image_payloads[image_index][0], image_index), factor)
    # A source image/texture may be shared by materials with different factors.
    # Give each material its own baked diffuse while leaving normal/specular users alone.
    gltf.images.append(Image(uri=_data_uri(raw, "image/png"), mimeType="image/png"))
    image_payloads.append((raw, "image/png"))
    texture = copy.deepcopy(gltf.textures[texture_index])
    texture.source = len(gltf.images) - 1
    gltf.textures.append(texture)
    info = copy.deepcopy(pbr.baseColorTexture)
    info.index = len(gltf.textures) - 1
    pbr.baseColorTexture = info


def prepare_materials(gltf_path: str, output_path: str, *, bake_size: int = 1024,
                      normal_y_sign: int = 1) -> str:
    """Write and return a self-contained glTF suitable for packaged NIF conversion."""
    if not os.path.isfile(gltf_path):
        raise AnyError(f"cannot read source glTF: {gltf_path}", code=1)
    try:
        gltf = GLTF2().load(gltf_path)
    except Exception as exc:
        raise AnyError(f"cannot parse glTF: {exc}", code=2) from exc
    source_dir = os.path.dirname(os.path.abspath(gltf_path))
    try:
        buffers = _buffers(gltf, source_dir)
    except AnyError:
        raise
    except Exception as exc:
        raise AnyError(f"cannot read glTF buffers: {exc}", code=2) from exc

    gltf.images = gltf.images or []
    gltf.textures = gltf.textures or []
    payloads: list[tuple[bytes, str]] = []
    for index in range(len(gltf.images)):
        payloads.append(_image_bytes(gltf, buffers, source_dir, index))

    for texture_index in _used_texture_indices(gltf):
        if texture_index < 0 or texture_index >= len(gltf.textures):
            raise AnyError(f"material references missing texture {texture_index}", code=2)
        texture = gltf.textures[texture_index]
        source = texture.source
        if source is None or source < 0 or source >= len(payloads):
            extensions = getattr(texture, "extensions", None) or {}
            if extensions:
                names = ", ".join(sorted(extensions))
                raise AnyError(
                    f"texture {texture_index} uses unsupported image-source extension(s): {names}",
                    code=2,
                )
            raise AnyError(f"texture {texture_index} references a missing image", code=2)

    for index, (raw, mime) in enumerate(payloads):
        gltf.images[index].uri = _data_uri(raw, mime)
        gltf.images[index].bufferView = None
        gltf.images[index].mimeType = mime
    for index, (buffer, raw) in enumerate(zip(gltf.buffers or [], buffers)):
        buffer.uri = _data_uri(raw, "application/octet-stream")
        buffer.byteLength = len(raw)

    gltf.materials = gltf.materials or []
    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            if primitive.material is None:
                primitive.material = len(gltf.materials)
                gltf.materials.append(Material())
    from .package_bake import bake_material_uvs, _needs_bake
    if any(_needs_bake(m) for m in gltf.materials):
        from gltf2nif import read_gltf, GltfError
        from .bake_geometry import flatten_instances
        try:
            read_gltf(gltf_path)  # Validate static geometry before atlas work or scene flattening.
            flatten_instances(gltf, buffers)
        except (GltfError, ValueError, IndexError, TypeError) as exc:
            message = str(exc)
            code = 3 if any(word in message for word in ("skinning", "morph", "animated")) else 2
            raise AnyError(message, code=code) from exc
    bake_material_uvs(gltf, buffers, payloads, size=bake_size, normal_y_sign=normal_y_sign)
    from .package_uv import prepare_uvs
    prepare_uvs(gltf, buffers)

    for index, material in enumerate(gltf.materials):
        _validate_texture_metadata(material, index)
        material.name = f"material_{index:04d}"
        _ensure_diffuse(gltf, material, payloads)

    destination = os.path.abspath(output_path)
    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        gltf.save_json(destination)
    except OSError as exc:
        raise AnyError(f"cannot write prepared glTF '{output_path}': {exc}", code=1) from exc
    return output_path

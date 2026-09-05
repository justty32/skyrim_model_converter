r"""Image -> mip chain -> BC1/BC3 -> .dds, the whole conversion in one place.

Pipeline order (each step optional where noted):

1. **Decode** with Pillow and normalise to RGBA uint8.
2. **Resize** to a power of two (``--resize pow2``, the default -- Skyrim's
   renderer wants power-of-two textures; ``none`` keeps the source dimensions).
3. **Normal-map fixups** (``--normal-map``): Skyrim's ``_n`` maps follow the
   DirectX convention (green = Y down) while glTF / OpenGL sources are Y up, so
   the green channel is inverted; the alpha channel of a Skyrim ``_n`` carries
   glossiness (the specular mask), filled with 255 when the source has none.
4. **Mip chain**: box-filtered halving from the previous level all the way to
   1x1, because Skyrim expects a complete chain.
5. **Encode** each level. Levels smaller than 4 pixels are padded out to the
   4x4 block grid by edge replication (never by black, which would bleed dark
   fringes into the smallest mips); the header still reports the true size.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

from . import dds
from .bcn import encode_bc1, encode_bc3
from .errors import Tex2ddsError

FORMATS = ("auto", "bc1", "bc3")
RESIZE_MODES = ("pow2", "none")
DEFAULT_FORMAT = "auto"
DEFAULT_RESIZE = "pow2"


def _nearest_pow2(value: int) -> int:
    """Closest power of two in log space, floored at 1."""
    if value < 1:
        return 1
    lo = 1 << (value.bit_length() - 1)
    hi = lo << 1
    return lo if (value - lo) <= (hi - value) else hi


def load_image(path: str) -> Image.Image:
    """Decode any Pillow-readable image to RGBA. Raises Tex2ddsError."""
    if not os.path.isfile(path):
        raise Tex2ddsError(f"cannot read source: {path}", 1)
    try:
        with Image.open(path) as img:
            img.load()
            return img.convert("RGBA")
    except Tex2ddsError:
        raise
    except Exception as exc:  # noqa: BLE001 - anything Pillow rejects is a parse failure
        raise Tex2ddsError(f"cannot decode image: {exc}", 2) from exc


def _as_rgba_array(image) -> np.ndarray:
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)
    arr = np.asarray(image)
    if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise Tex2ddsError("image array must be uint8 with shape (H, W, 3|4)", 1)
    if arr.shape[2] == 3:
        arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), 255, np.uint8)], axis=2)
    return np.ascontiguousarray(arr)


def has_real_alpha(rgba: np.ndarray) -> bool:
    """True when the alpha channel actually carries transparency (not all opaque)."""
    return bool(rgba.shape[2] == 4 and rgba[:, :, 3].min() < 255)


def choose_format(rgba: np.ndarray, fmt: str = DEFAULT_FORMAT, normal_map: bool = False) -> str:
    """Resolve ``auto`` to bc1/bc3. ``--normal-map`` forces bc3 to keep the gloss alpha."""
    if fmt not in FORMATS:
        raise Tex2ddsError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}", 1)
    if normal_map:
        return "bc3"
    if fmt != "auto":
        return fmt
    return "bc3" if has_real_alpha(rgba) else "bc1"


def apply_normal_map(rgba: np.ndarray) -> np.ndarray:
    """DirectX-convention green flip; alpha (glossiness) left as-is, 255 when absent."""
    out = rgba.copy()
    out[:, :, 1] = 255 - out[:, :, 1]
    return out


def build_mip_chain(rgba: np.ndarray, mipmaps: bool = True) -> list[np.ndarray]:
    """Box-filtered chain down to 1x1 (or just the base level when mipmaps is False)."""
    base = Image.fromarray(rgba, "RGBA")
    levels = [rgba]
    if not mipmaps:
        return levels
    current = base
    w, h = current.size
    while w > 1 or h > 1:
        w = max(1, w // 2)
        h = max(1, h // 2)
        current = current.resize((w, h), Image.Resampling.BOX)
        levels.append(np.asarray(current, dtype=np.uint8))
    return levels


def _pad_to_blocks(level: np.ndarray) -> np.ndarray:
    h, w = level.shape[:2]
    pad_h = (-h) % 4
    pad_w = (-w) % 4
    if not pad_h and not pad_w:
        return level
    return np.pad(level, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")


def encode_level(level: np.ndarray, fmt: str) -> bytes:
    """Compress one mip level (any size; padded internally to the 4x4 grid)."""
    padded = _pad_to_blocks(level)
    return encode_bc1(padded) if fmt == "bc1" else encode_bc3(padded)


def encode_dds(image, *, fmt: str = DEFAULT_FORMAT, mipmaps: bool = True,
               normal_map: bool = False, resize: str = DEFAULT_RESIZE) -> bytes:
    """Full .dds bytes for a PIL image or an (H, W, 3|4) uint8 array.

    ``fmt`` is ``auto`` / ``bc1`` / ``bc3``; ``resize`` is ``pow2`` / ``none``.
    """
    if resize not in RESIZE_MODES:
        raise Tex2ddsError(f"unknown resize mode {resize!r}; expected pow2 or none", 1)
    rgba = _as_rgba_array(image)
    if rgba.shape[0] < 1 or rgba.shape[1] < 1:
        raise Tex2ddsError("image has no pixels", 2)

    if resize == "pow2":
        h, w = rgba.shape[:2]
        tw, th = _nearest_pow2(w), _nearest_pow2(h)
        if (tw, th) != (w, h):
            resized = Image.fromarray(rgba, "RGBA").resize((tw, th), Image.Resampling.LANCZOS)
            rgba = np.asarray(resized, dtype=np.uint8)

    resolved = choose_format(rgba, fmt, normal_map)
    if normal_map:
        rgba = apply_normal_map(rgba)

    levels = build_mip_chain(rgba, mipmaps)
    height, width = rgba.shape[:2]
    payload = b"".join(encode_level(level, resolved) for level in levels)
    return dds.build_header(width, height, resolved, len(levels)) + payload


def convert_file(in_path: str, out_path: str, *, fmt: str = DEFAULT_FORMAT,
                 mipmaps: bool = True, normal_map: bool = False,
                 resize: str = DEFAULT_RESIZE) -> dict:
    """Convert one image file to a .dds on disk. Returns a small summary dict."""
    image = load_image(in_path)
    data = encode_dds(image, fmt=fmt, mipmaps=mipmaps, normal_map=normal_map, resize=resize)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        raise Tex2ddsError(f"cannot write {out_path}: {exc}", 1) from exc
    info = dds.parse_header(data)
    return {
        "path": out_path,
        "width": info["width"],
        "height": info["height"],
        "format": "bc1" if info["fourcc"] == b"DXT1" else "bc3",
        "mip_count": info["mip_count"],
        "bytes": len(data),
    }

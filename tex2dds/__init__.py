"""tex2dds - common image formats -> Skyrim .dds (BC1 / BC3, full mip chain).

Pure numpy + Pillow: the BC1/BC3 block compressors and the DDS header are
written here, so there is no external binary (texconv / nvcompress / ImageMagick)
anywhere in the path and the output stays reproducible offline.
"""

from __future__ import annotations

from .bcn import encode_bc1, encode_bc3
from .dds import build_header, full_mip_count, linear_size, mip_sizes, parse_header
from .errors import Tex2ddsError
from .pipeline import (
    DEFAULT_FORMAT,
    DEFAULT_RESIZE,
    FORMATS,
    RESIZE_MODES,
    build_mip_chain,
    choose_format,
    convert_file,
    encode_dds,
    has_real_alpha,
    load_image,
)

__all__ = [
    "Tex2ddsError",
    "encode_dds",
    "convert_file",
    "load_image",
    "choose_format",
    "has_real_alpha",
    "build_mip_chain",
    "encode_bc1",
    "encode_bc3",
    "build_header",
    "parse_header",
    "linear_size",
    "mip_sizes",
    "full_mip_count",
    "FORMATS",
    "RESIZE_MODES",
    "DEFAULT_FORMAT",
    "DEFAULT_RESIZE",
]

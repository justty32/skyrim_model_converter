r"""tex2dds CLI. Exit codes: 0 ok / 1 general / 2 source image parse failure.

    python -m tex2dds <in.{png,jpg,jpeg,tga,bmp,dds}> <out.dds>
          [--format auto|bc1|bc3] [--no-mipmaps] [--normal-map] [--resize pow2|none]
"""

from __future__ import annotations

import argparse
import sys

from .errors import Tex2ddsError
from .pipeline import DEFAULT_FORMAT, DEFAULT_RESIZE, FORMATS, RESIZE_MODES, convert_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tex2dds",
        description="PNG/JPG/TGA/BMP/DDS -> Skyrim .dds (BC1/BC3, full mip chain). "
                    "Pure Python, no external tool.",
    )
    parser.add_argument("in_path", help="source image (png/jpg/jpeg/tga/bmp/dds)")
    parser.add_argument("out_path", help="target .dds")
    parser.add_argument("--format", dest="fmt", default=DEFAULT_FORMAT, choices=FORMATS,
                        help="auto picks BC3 when the image has real alpha, else BC1")
    parser.add_argument("--no-mipmaps", dest="mipmaps", action="store_false",
                        help="write only the base level (default: full chain to 1x1)")
    parser.add_argument("--normal-map", action="store_true",
                        help="Skyrim _n map: flip green to the DirectX convention, "
                             "keep alpha as glossiness, force BC3")
    parser.add_argument("--resize", default=DEFAULT_RESIZE, choices=RESIZE_MODES,
                        help="pow2 (default) snaps to the nearest power of two; "
                             "none keeps the source dimensions")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on a bad command line; PROTOCOL.md reserves 2 for a
        # source parse failure, so argument errors come back as 1 (--help stays 0).
        return 1 if exc.code else 0
    try:
        info = convert_file(args.in_path, args.out_path, fmt=args.fmt, mipmaps=args.mipmaps,
                            normal_map=args.normal_map, resize=args.resize)
    except Tex2ddsError as exc:
        label = "parse error" if exc.code == 2 else "error"
        print(f"{label}: {args.in_path}: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001
        print(f"error: {args.in_path}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {info['path']}: {info['width']}x{info['height']} "
          f"{info['format'].upper()}, {info['mip_count']} mip(s), {info['bytes']} bytes",
          file=sys.stderr)
    return 0

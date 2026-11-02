"""Rebuild the tiny, original box GLB used by the ModForge live contract."""

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import struct

from PIL import Image


def build(path):
    # Deliberately asymmetric, offset box: axis/scale mistakes remain visible.
    positions = [(x, y, z) for z in (3, 7) for y in (2, 4) for x in (1, 2)]
    indices = [0, 2, 3, 0, 3, 1, 4, 5, 7, 4, 7, 6,
               0, 1, 5, 0, 5, 4, 2, 6, 7, 2, 7, 3,
               0, 4, 6, 0, 6, 2, 1, 3, 7, 1, 7, 5]
    binary = b"".join(struct.pack("<8f", *p, 0, 1, 0, i % 2, (i // 2) % 2)
                      for i, p in enumerate(positions))
    binary += struct.pack("<36H", *indices)
    png = BytesIO()
    Image.new("RGB", (8, 8), (224, 72, 24)).save(png, format="PNG")
    document = {
        "asset": {"version": "2.0", "generator": "original synthetic box fixture"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": 256, "byteStride": 32, "target": 34962},
                        {"buffer": 0, "byteOffset": 256, "byteLength": 72, "target": 34963}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
             "min": [1, 2, 3], "max": [2, 4, 7]},
            {"bufferView": 0, "byteOffset": 12, "componentType": 5126, "count": 8, "type": "VEC3"},
            {"bufferView": 0, "byteOffset": 24, "componentType": 5126, "count": 8, "type": "VEC2"},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                       "indices": 3, "material": 0}]}],
        "images": [{"uri": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()}],
        "textures": [{"source": 0}],
        "materials": [{"name": "OriginalOrange", "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": 1}}],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    payload = struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary))
    payload += struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    payload += struct.pack("<II", len(binary), 0x004E4942) + binary
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)

"""glTF material images -> Skyrim .dds files. (skeleton)"""

from __future__ import annotations


def export_textures(gltf_path: str, out_dir: str, *, codec: str = "auto") -> dict[str, dict[str, str]]:
    """material basename -> {"diffuse": file, "normal": file, "specular": file}."""
    return {}

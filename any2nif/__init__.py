"""any2nif - common model formats -> Skyrim SSE .nif (+ .dds textures).

Front door for the forward pipeline. Normalises any supported source format to glTF,
then reuses gltf2nif (byte-verified against vanilla SSE nifs) for the NIF itself.
"""

from __future__ import annotations

from .errors import AnyError
from .normalize import (
    FBX_EXTENSIONS,
    NATIVE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    TRIMESH_EXTENSIONS,
    normalize_to_gltf,
)

__all__ = [
    "AnyError",
    "normalize_to_gltf",
    "SUPPORTED_EXTENSIONS",
    "NATIVE_EXTENSIONS",
    "TRIMESH_EXTENSIONS",
    "FBX_EXTENSIONS",
]

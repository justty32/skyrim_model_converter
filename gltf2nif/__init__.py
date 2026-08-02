"""gltf2nif — glTF static mesh -> Skyrim SSE .nif writer (reverse of nif2gltf)."""

from __future__ import annotations

from ._binwriter import GltfError
from .collision import Hull, load_hulls
from .geometry import Mesh
from .gltf_reader import read_gltf
from .nif_writer import build_nif

__all__ = ["build_nif", "read_gltf", "load_hulls", "Mesh", "Hull", "GltfError"]

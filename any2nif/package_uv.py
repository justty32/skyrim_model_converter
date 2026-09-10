"""Bake a shared material UV mapping into the package's single NIF UV set.

Transform order follows Khronos KHR_texture_transform: offset + rotation * scale * UV.
"""

import base64
import math

import numpy as np
from pygltflib import Accessor, Buffer, BufferView

from gltf2nif.gltf_reader import _read_accessor
from .errors import AnyError


def _mapping(info, label):
    from .package_materials import _field

    ext = (_field(info, "extensions", {}) or {}).get("KHR_texture_transform", {})
    if not isinstance(ext, dict):
        raise AnyError(f"{label}: malformed KHR_texture_transform", code=2)
    coord = ext.get("texCoord", _field(info, "texCoord", 0))
    coord = 0 if coord is None else coord
    if isinstance(coord, bool) or not isinstance(coord, int) or coord < 0:
        raise AnyError(f"{label}: texCoord must be a nonnegative integer", code=2)
    offset, scale = ext.get("offset", [0, 0]), ext.get("scale", [1, 1])
    rotation = ext.get("rotation", 0)
    for value in (offset, scale):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise AnyError(f"{label}: UV offset/scale must have two numbers", code=2)
    values = [*offset, *scale, rotation]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           not math.isfinite(v) for v in values):
        raise AnyError(f"{label}: UV transform must contain finite numbers", code=2)
    return coord, tuple(offset), tuple(scale), rotation


def prepare_uvs(gltf, buffers):
    from .package_materials import _material_texture_infos

    mappings = {}
    identity = (0, (0, 0), (1, 1), 0)
    for mi, material in enumerate(gltf.materials):
        infos = list(_material_texture_infos(material))
        mapping = None
        for slot, info in infos:
            current = _mapping(info, f"material {mi} {slot}")
            if mapping is not None and current != mapping:
                raise AnyError(f"material {mi}: texture slots use different UV mappings; "
                               "bake them to one UV set before conversion", code=2)
            mapping = current
        if mapping is None or mapping == identity:
            continue
        coord, offset, scale, rotation = mapping
        if material.normalTexture is not None and (
            rotation != 0 or scale[0] <= 0 or scale[0] != scale[1]
        ):
            raise AnyError(f"material {mi}: normal texture UV transform requires "
                           "positive uniform scale and no rotation", code=2)
        mappings[mi] = mapping

    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            mapping = mappings.get(primitive.material)
            if mapping is None:
                continue
            coord, offset, scale, rotation = mapping
            label = f"material {primitive.material} TEXCOORD_{coord}"
            index = getattr(primitive.attributes, f"TEXCOORD_{coord}", None)
            if not isinstance(index, int) or not 0 <= index < len(gltf.accessors):
                raise AnyError(f"{label}: missing UV accessor", code=2)
            accessor = gltf.accessors[index]
            valid = accessor.type == "VEC2" and (
                accessor.componentType == 5126 and not accessor.normalized or
                accessor.componentType in (5121, 5123) and accessor.normalized)
            if not valid:
                raise AnyError(f"{label}: invalid UV components", code=2)
            try:
                uv = np.asarray(_read_accessor(gltf, buffers, index), dtype=np.float64)
                position = gltf.accessors[primitive.attributes.POSITION]
                if uv.shape != (position.count, 2) or not np.isfinite(uv).all():
                    raise ValueError("UV count or values are invalid")
                c, s = math.cos(rotation), math.sin(rotation)
                with np.errstate(over="ignore", invalid="ignore"):
                    uv = (uv * scale) @ np.array([[c, s], [-s, c]]) + offset
                # BSTriShape stores half precision UVs; catch overflow before writing.
                if not np.isfinite(uv).all() or (np.abs(uv) > 65504).any():
                    raise ValueError("transformed UV exceeds NIF half precision range")
            except Exception as exc:
                raise AnyError(f"{label}: {exc}", code=2) from exc
            raw = uv.astype("<f4").tobytes()
            gltf.buffers.append(Buffer(byteLength=len(raw), uri=
                "data:application/octet-stream;base64," + base64.b64encode(raw).decode()))
            gltf.bufferViews.append(BufferView(buffer=len(gltf.buffers) - 1,
                                              byteLength=len(raw)))
            gltf.accessors.append(Accessor(bufferView=len(gltf.bufferViews) - 1,
                componentType=5126, count=len(uv), type="VEC2"))
            primitive.attributes.TEXCOORD_0 = len(gltf.accessors) - 1

    for mi in mappings:
        for _, info in _material_texture_infos(gltf.materials[mi]):
            if isinstance(info, dict):
                info["texCoord"] = 0
                (info.get("extensions") or {}).pop("KHR_texture_transform", None)
            else:
                info.texCoord = 0
                (info.extensions or {}).pop("KHR_texture_transform", None)

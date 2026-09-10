"""Re-sample material slots onto an independent atlas per source primitive.

AO is multiplied into linear diffuse as a traditional Skyrim approximation.
UV packing: https://github.com/mworchel/xatlas-python
Material channels: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#materials
"""

import copy
import io
import math
import sys

import numpy as np
from PIL import Image as PILImage
from pygltflib import Accessor, Buffer, BufferView, Image, Texture, TextureInfo, PbrMetallicRoughness

from gltf2nif.gltf_reader import _read_accessor
from gltf2nif import GltfError
from gltf2nif.geometry import face_normals
from .errors import AnyError
from .package_materials import _field, _material_texture_infos, _data_uri, _decode_rgba
from .package_uv import _mapping


def append_accessor(gltf, buffers, values, kind, *, indices=False):
    values = np.asarray(values, dtype='<u4' if indices else '<f4')
    raw = values.tobytes()
    gltf.buffers.append(Buffer(byteLength=len(raw), uri=_data_uri(raw, 'application/octet-stream')))
    buffers.append(raw)
    gltf.bufferViews.append(BufferView(buffer=len(buffers) - 1, byteLength=len(raw)))
    gltf.accessors.append(Accessor(bufferView=len(gltf.bufferViews) - 1,
        componentType=5125 if indices else 5126, count=len(values), type=kind))
    return len(gltf.accessors) - 1


def read_uv(gltf, buffers, primitive, mapping, count):
    coord, offset, scale, rotation = mapping
    index = getattr(primitive.attributes, f'TEXCOORD_{coord}', None)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(gltf.accessors):
        raise ValueError(f'TEXCOORD_{coord}: missing UV accessor')
    acc = gltf.accessors[index]
    if acc.type != 'VEC2' or not (
        acc.componentType == 5126 and not acc.normalized or
        acc.componentType in (5121, 5123) and acc.normalized
    ):
        raise ValueError(f'TEXCOORD_{coord}: invalid UV components')
    uv = np.asarray(_read_accessor(gltf, buffers, index), dtype=np.float64)
    if uv.shape != (count, 2) or not np.isfinite(uv).all():
        raise ValueError(f'TEXCOORD_{coord}: invalid count or values')
    c, s = math.cos(rotation), math.sin(rotation)
    with np.errstate(over='ignore', invalid='ignore'):
        mapped = (uv * scale) @ np.array([[c, s], [-s, c]]) + offset
    if not np.isfinite(mapped).all() or (np.abs(mapped) > 65504).any():
        raise ValueError('transformed UV exceeds supported precision range')
    return uv, mapped


def _sampling(gltf, info):
    index = _field(info, 'index')
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(gltf.textures):
        raise AnyError('texture-info references missing texture', code=2)
    texture = gltf.textures[index]
    sampler = None
    if texture.sampler is not None:
        index = texture.sampler
        if (isinstance(index, bool) or not isinstance(index, int) or
                not 0 <= index < len(gltf.samplers or [])):
            raise AnyError('texture references missing sampler', code=2)
        sampler = gltf.samplers[index]

    def enum(field, choices, default=None):
        value = getattr(sampler, field, None)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value not in choices:
            raise AnyError(f'invalid texture {field}', code=2)
        return value

    mag = enum('magFilter', (9728, 9729), 9729)
    enum('minFilter', (9728, 9729, 9984, 9985, 9986, 9987))
    return dict(wrap_s=enum('wrapS', (10497, 33648, 33071), 10497),
                wrap_t=enum('wrapT', (10497, 33648, 33071), 10497),
                linear=mag != 9728)


def _needs_bake(gltf, material):
    infos = list(_material_texture_infos(material))
    mappings = [_mapping(info, slot) for slot, info in infos]
    # The NIF writer has no per-texture sampler controls. Bake non-default
    # sampling even when every slot shares the same UV set and transform.
    sampling = [_sampling(gltf, info) for _, info in infos]
    normal_transform = False
    if material.normalTexture is not None:
        _, _, scale, rotation = _mapping(material.normalTexture, 'normalTexture')
        if scale[0] == 0 or scale[1] == 0:
            raise AnyError('normal texture UV transform requires nonzero scale', code=2)
        normal_transform = rotation != 0 or scale[0] < 0 or scale[0] != scale[1]
    return (normal_transform or len(set(mappings)) > 1 or material.occlusionTexture is not None or
            any(s['wrap_s'] != 10497 or s['wrap_t'] != 10497 or not s['linear']
                for s in sampling))


def _image(gltf, payloads, info):
    options = _sampling(gltf, info)
    texture = gltf.textures[_field(info, 'index')]
    return _decode_rgba(payloads[texture.source][0], texture.source), options


def _set_image(gltf, payloads, info, pixels, *, srgb=False):
    encoded = np.empty(pixels.shape, dtype=np.uint8)
    for start in range(0, len(pixels), 64):
        part = pixels[start:start + 64].copy()
        if srgb:
            rgb = np.clip(part[..., :3], 0, 1)
            part[..., :3] = np.where(rgb <= .0031308, rgb * 12.92,
                                    1.055 * rgb ** (1 / 2.4) - .055)
        encoded[start:start + 64] = np.clip(np.rint(part * 255), 0, 255).astype(np.uint8)
    output = io.BytesIO()
    PILImage.fromarray(encoded).save(output, format='PNG')
    raw = output.getvalue()
    gltf.images.append(Image(uri=_data_uri(raw, 'image/png'), mimeType='image/png'))
    payloads.append((raw, 'image/png'))
    gltf.textures.append(Texture(source=len(gltf.images) - 1))
    if isinstance(info, dict):
        info['index'], info['texCoord'] = len(gltf.textures) - 1, 0
        (info.get('extensions') or {}).pop('KHR_texture_transform', None)
    else:
        info.index, info.texCoord = len(gltf.textures) - 1, 0
        (info.extensions or {}).pop('KHR_texture_transform', None)


def _bake_primitive(gltf, buffers, payloads, primitive, material, size):
    from .texture_sampling import sample_rgba
    from .uv_atlas import generate_atlas, ensure_face_coverage, dilate_pixels
    from .normal_bake import source_corner_frames, target_corner_frames, interpolate_frames, reorient_normals

    attrs = primitive.attributes
    positions = np.asarray(_read_accessor(gltf, buffers, attrs.POSITION), dtype=np.float64)
    flat = (_read_accessor(gltf, buffers, primitive.indices) if primitive.indices is not None
            else np.arange(len(positions)))
    triangles = np.asarray(flat, dtype=np.int64).reshape(-1, 3)
    normals = np.asarray(_read_accessor(gltf, buffers, attrs.NORMAL) if attrs.NORMAL is not None
                         else face_normals(positions, triangles), dtype=np.float64)
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths < 1e-12) or not np.isfinite(normals).all():
        raise ValueError('invalid normals for UV baking')
    normals /= lengths[:, None]
    infos = list(_material_texture_infos(material))
    # Read every source mapping before changing any attributes or texture-info.
    maps = {slot: read_uv(gltf, buffers, primitive, _mapping(info, slot), len(positions))
            for slot, info in infos}
    vmapping, output_triangles, output_uv = generate_atlas(positions, triangles, normals, size)
    vmapping, output_triangles, output_uv, owners, bary = ensure_face_coverage(
        vmapping, output_triangles, output_uv, size)
    covered = owners >= 0
    pixel_indices = np.flatnonzero(covered)
    owners_flat, bary_flat = owners.reshape(-1), bary.reshape(-1, 3)
    ao_info = material.occlusionTexture
    ao_source = None
    if ao_info is not None:
        strength = ao_info.strength
        strength = 1 if strength is None else strength
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not 0 <= strength <= 1:
            raise ValueError('occlusionTexture strength must be finite and between 0 and 1')
        ao_source = _image(gltf, payloads, ao_info)
        if not any(slot == 'baseColorTexture' for slot, _ in infos):
            material.pbrMetallicRoughness = material.pbrMetallicRoughness or PbrMetallicRoughness()
            material.pbrMetallicRoughness.baseColorTexture = TextureInfo(index=0)
            infos.append(('baseColorTexture', material.pbrMetallicRoughness.baseColorTexture))

    for slot, info in infos:
        if slot == 'occlusionTexture':
            continue
        solid_base = slot == 'baseColorTexture' and slot not in maps
        if not solid_base:
            rgba, options = _image(gltf, payloads, info)
        srgb = slot in ('baseColorTexture', 'emissiveTexture', 'specularColorTexture', 'sheenColorTexture')
        if slot == 'normalTexture':
            tangent_index = getattr(attrs, 'TANGENT', None)
            tangents = (_read_accessor(gltf, buffers, tangent_index)
                        if tangent_index is not None else None)
            _, _, uv_scale, rotation = _mapping(info, slot)
            c, s = math.cos(rotation), math.sin(rotation)
            uv_matrix = np.diag(uv_scale) @ np.array([[c, s], [-s, c]])
            src_frames = source_corner_frames(positions, normals, maps[slot][1], triangles,
                                       tangents=tangents,
                                       uv_transform=uv_matrix if tangents is not None else None).reshape(-1, 3, 3)
            src_triangles = np.arange(len(src_frames)).reshape(-1, 3)
            dst_frames = target_corner_frames(positions[vmapping], normals[vmapping],
                                               output_uv, output_triangles).reshape(-1, 3, 3)
            dst_triangles = np.arange(len(dst_frames)).reshape(-1, 3)
            scale = _field(info, 'scale', 1)
        # Complete and encode one slot before allocating another atlas image.
        pixels = np.zeros((size, size, 4), dtype=np.float32)
        flat_pixels = pixels.reshape(-1, 4)
        for start in range(0, len(pixel_indices), 65536):
            selected = pixel_indices[start:start + 65536]
            face_ids, weights = owners_flat[selected], bary_flat[selected]
            corners = triangles[face_ids]
            if solid_base:
                values = np.ones((len(selected), 4), dtype=np.float64)
            else:
                source_uv = np.einsum('ni,nij->nj', weights, maps[slot][1][corners])
                values = sample_rgba(rgba, source_uv, srgb=srgb, **options)
            if slot == 'baseColorTexture' and ao_source is not None:
                ao_uv = np.einsum('ni,nij->nj', weights, maps['occlusionTexture'][1][corners])
                ao = sample_rgba(ao_source[0], ao_uv, **ao_source[1])[:, 0]
                values[:, :3] *= (1 + strength * (ao - 1))[:, None]
            if slot == 'normalTexture':
                src = interpolate_frames(src_frames, src_triangles, face_ids, weights)
                dst = interpolate_frames(dst_frames, dst_triangles, face_ids, weights)
                values = reorient_normals(values, src, dst, scale=1 if scale is None else scale)
            flat_pixels[selected] = values
        if slot == 'normalTexture':
            info.scale = 1.0
        del flat_pixels
        pixels = dilate_pixels(pixels, owners, padding=size * 2)
        _set_image(gltf, payloads, info, pixels, srgb=srgb)
        del pixels
    material.occlusionTexture = None
    # Atlas seams duplicate vertices, so every attribute must follow the same map.
    for name, index in list(vars(attrs).items()):
        if index is None or not isinstance(index, int):
            continue
        acc = gltf.accessors[index]
        values = np.asarray(_read_accessor(gltf, buffers, index))[vmapping]
        setattr(attrs, name, append_accessor(gltf, buffers, values, acc.type))
    attrs.NORMAL = append_accessor(gltf, buffers, normals[vmapping], 'VEC3')
    attrs.TEXCOORD_0 = append_accessor(gltf, buffers, output_uv, 'VEC2')
    # Target writer computes its own basis from the atlas UV.
    attrs.TANGENT = None
    primitive.indices = append_accessor(gltf, buffers, output_triangles.reshape(-1), 'SCALAR', indices=True)


def bake_material_uvs(gltf, buffers, payloads, *, size=1024):
    """Bake conflicting texture mappings (and AO) before shared-UV preparation."""
    if isinstance(size, bool) or size not in (64, 128, 256, 512, 1024, 2048, 4096):
        raise AnyError("UV bake size must be a power of two from 64 to 4096", code=2)
    originals = copy.deepcopy(gltf.materials)
    baked = set()
    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            mi = primitive.material
            if mi is None or not 0 <= mi < len(originals) or not _needs_bake(gltf, originals[mi]):
                continue
            material = copy.deepcopy(originals[mi])
            print(f'baking material {mi}: {size} x {size} UV atlas', file=sys.stderr, flush=True)
            try:
                _bake_primitive(gltf, buffers, payloads, primitive, material, size)
            except (ValueError, IndexError, TypeError, GltfError) as exc:
                raise AnyError(f'material {mi}: UV bake: {exc}', code=2) from exc
            if mi in baked:
                primitive.material = len(gltf.materials)
                gltf.materials.append(material)
            else:
                gltf.materials[mi] = material
                baked.add(mi)

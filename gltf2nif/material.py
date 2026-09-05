"""glTF 2.0 PBR material -> Skyrim BSLightingShaderProperty parameters.

glTF is metallic/roughness PBR; Skyrim's BSLightingShaderProperty is a
Blinn-Phong-era shader with a glossiness exponent and a scalar specular
strength. There is NO physically correct mapping between the two, so
everything in here is a **perceptual approximation** tuned to look sane on
ported static meshes -- not an energy-conserving conversion. Each formula
carries its own note; if a port looks wrong, tweak the numbers, don't expect
them to be derivable.

This module only *reads* glTF and produces `MaterialSpec` values. The actual
byte emission lives in `nif_writer`. Nothing here is used unless the caller
explicitly passes `material_specs=` to `build_nif`, so the historical
(material-free) output is untouched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# glTF spec defaults, used whenever a material omits the field.
_DEFAULT_BASE_COLOR = (1.0, 1.0, 1.0, 1.0)
_DEFAULT_EMISSIVE = (0.0, 0.0, 0.0)

_EXT_EMISSIVE_STRENGTH = "KHR_materials_emissive_strength"
_EXT_SPECULAR = "KHR_materials_specular"


@dataclass
class MaterialSpec:
    """One glTF material, reduced to what a BSLightingShaderProperty can express."""

    base_name: str = ""            # texture base name (== Mesh.material)
    base_color: tuple[float, float, float, float] = _DEFAULT_BASE_COLOR
    metallic: float = 1.0
    roughness: float = 1.0
    emissive: tuple[float, float, float] = _DEFAULT_EMISSIVE
    emissive_strength: float = 1.0
    alpha_mode: str = "OPAQUE"     # OPAQUE | MASK | BLEND
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    has_normal_map: bool = False
    has_specular_map: bool = False
    # --- additive fields (appended, defaulted; never reorder the block above) ---
    has_emissive_map: bool = False
    # KHR_materials_specular specularFactor (1.0 == "extension absent").
    specular_factor: float = 1.0
    # Exact NIF texture path for slot 1. Empty preserves normal-map probing/fallback.
    normal_texture_name: str = ""
    # Optional raw NiAlphaProperty overrides for callers with engine-tested values.
    alpha_flags_override: int | None = None
    alpha_threshold_override: int | None = None
    # "effect" selects BSEffectShaderProperty; every other value uses lighting.
    shader_kind: str = "lighting"


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


def roughness_to_glossiness(roughness: float) -> float:
    """Perceptual roughness [0,1] -> BSLightingShaderProperty Glossiness exponent.

    PERCEPTUAL APPROXIMATION, NOT A PHYSICAL EQUIVALENCE. Skyrim's glossiness is a
    Blinn-Phong specular exponent; glTF roughness is a microfacet parameter. We map
    them on the usual "exponent doubles per perceptual step" curve:

        gloss = clamp(2 ** ((1 - roughness) * 9 + 1), 2, 999)

    roughness 1.0 -> 2 (matte), 0.5 -> ~45, 0.0 -> 1024 clamped to 999 (the engine
    misbehaves well before four digits, and vanilla statics sit around 30-100).
    """
    r = _clamp(float(roughness), 0.0, 1.0)
    return _clamp(2.0 ** ((1.0 - r) * 9.0 + 1.0), 2.0, 999.0)


def metallic_to_specular_strength(metallic: float, specular_factor: float = 1.0) -> float:
    """Metallic [0,1] (x KHR specularFactor) -> Specular Strength [0,1].

    PERCEPTUAL APPROXIMATION. Dielectrics in glTF still have a ~4% Fresnel specular,
    so the floor is 0.2 rather than 0 (0 reads as dead plastic in Skyrim's lighting):

        strength = clamp((0.2 + 0.8 * metallic) * specular_factor, 0, 1)

    With KHR_materials_specular absent, specular_factor is 1.0 and the formula
    reduces to the plain metallic ramp.
    """
    m = _clamp(float(metallic), 0.0, 1.0)
    return _clamp((0.2 + 0.8 * m) * float(specular_factor), 0.0, 1.0)


def metallic_specular_color(base_color, metallic: float) -> tuple[float, float, float]:
    """Specular tint: white for dielectrics, base colour for metals, lerped between.

    PERCEPTUAL APPROXIMATION of the metallic workflow's F0 (dielectric F0 is a
    near-white ~0.04; a metal's F0 IS its base colour).
    """
    m = _clamp(float(metallic), 0.0, 1.0)
    r, g, b = (float(base_color[0]), float(base_color[1]), float(base_color[2]))
    return (1.0 + (r - 1.0) * m, 1.0 + (g - 1.0) * m, 1.0 + (b - 1.0) * m)


def _ext(material, name: str) -> dict:
    """Extension payload for `material`, or {} when absent/unusable."""
    exts = getattr(material, "extensions", None) or {}
    try:
        value = exts.get(name)
    except AttributeError:
        return {}
    return value if isinstance(value, dict) else {}


def _tuple(values, size: int, default):
    if not values:
        return default
    try:
        out = tuple(float(v) for v in list(values)[:size])
    except (TypeError, ValueError):
        return default
    if len(out) != size:
        return default
    return out


def _has_texture(info) -> bool:
    return info is not None and getattr(info, "index", None) is not None


def read_materials(gltf) -> list[MaterialSpec]:
    """pygltflib GLTF2 -> one MaterialSpec per glTF material, index-aligned.

    Missing fields fall back to the glTF defaults; unknown/absent KHR extensions are
    simply ignored (never an exception) so an exotic exporter cannot sink a port.
    """
    specs: list[MaterialSpec] = []
    for material in (getattr(gltf, "materials", None) or []):
        pbr = getattr(material, "pbrMetallicRoughness", None)
        base_color = _DEFAULT_BASE_COLOR
        metallic, roughness = 1.0, 1.0
        if pbr is not None:
            base_color = _tuple(getattr(pbr, "baseColorFactor", None), 4, _DEFAULT_BASE_COLOR)
            mf = getattr(pbr, "metallicFactor", None)
            rf = getattr(pbr, "roughnessFactor", None)
            metallic = 1.0 if mf is None else float(mf)
            roughness = 1.0 if rf is None else float(rf)

        emissive = _tuple(getattr(material, "emissiveFactor", None), 3, _DEFAULT_EMISSIVE)
        strength = _ext(material, _EXT_EMISSIVE_STRENGTH).get("emissiveStrength", 1.0)
        try:
            emissive_strength = float(strength)
        except (TypeError, ValueError):
            emissive_strength = 1.0

        specular_ext = _ext(material, _EXT_SPECULAR)
        try:
            specular_factor = float(specular_ext.get("specularFactor", 1.0))
        except (TypeError, ValueError):
            specular_factor = 1.0
        has_specular_map = bool(specular_ext.get("specularTexture")
                                or specular_ext.get("specularColorTexture"))

        alpha_mode = (getattr(material, "alphaMode", None) or "OPAQUE").upper()
        if alpha_mode not in ("OPAQUE", "MASK", "BLEND"):
            alpha_mode = "OPAQUE"
        cutoff = getattr(material, "alphaCutoff", None)

        specs.append(MaterialSpec(
            base_name=os.path.splitext(getattr(material, "name", "") or "")[0],
            base_color=base_color,
            metallic=metallic,
            roughness=roughness,
            emissive=emissive,
            emissive_strength=emissive_strength,
            alpha_mode=alpha_mode,
            alpha_cutoff=0.5 if cutoff is None else float(cutoff),
            double_sided=bool(getattr(material, "doubleSided", False)),
            has_normal_map=_has_texture(getattr(material, "normalTexture", None)),
            has_specular_map=has_specular_map,
            has_emissive_map=_has_texture(getattr(material, "emissiveTexture", None)),
            specular_factor=specular_factor,
        ))
    return specs


def specs_for_meshes(gltf_path: str, meshes) -> list["MaterialSpec | None"]:
    """Per-Mesh material specs, positionally aligned with `meshes`.

    `read_gltf` walks scene nodes x primitives, so the Mesh order is not the glTF
    material order. Rather than replay that walk (and risk drifting from it), the
    reader stamps `Mesh.material_index` with the primitive's material index and we
    just look it up. Meshes without a usable index fall back to a UNIQUE
    material-name match, else None.

    Always returns exactly len(meshes) entries. Signature is frozen: any2nif calls it.
    """
    from pygltflib import GLTF2

    meshes = list(meshes)
    gltf = GLTF2().load(gltf_path)
    specs = read_materials(gltf) if gltf is not None else []

    by_name: dict[str, MaterialSpec | None] = {}
    for spec in specs:
        if not spec.base_name:
            continue
        by_name[spec.base_name] = None if spec.base_name in by_name else spec

    out: list[MaterialSpec | None] = []
    for mesh in meshes:
        index = getattr(mesh, "material_index", -1)
        if isinstance(index, int) and 0 <= index < len(specs):
            out.append(specs[index])
            continue
        out.append(by_name.get(getattr(mesh, "material", "") or "", None))
    return out

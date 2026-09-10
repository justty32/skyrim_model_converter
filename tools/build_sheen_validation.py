"""Build fresh, self-checking SheenChair packages for home Skyrim validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from gltf2nif import read_gltf
from nif2gltf._binreader import _Reader
from nif2gltf.nif_reader import _read_header, read_nif
from tools.smoke_real_assets import ASSETS, REV, _corner_geometry, validate_sheen_diffuse

SOURCE_SHA256 = ASSETS["SheenChair"]
SOURCE_URL = ("https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/"
              f"{REV}/Models/SheenChair/glTF-Binary/SheenChair.glb")
ATTRIBUTION_URL = ("https://github.com/KhronosGroup/glTF-Sample-Assets/blob/"
                   f"{REV}/Models/SheenChair/README.md")
MANIFEST = "converter-package.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fresh_build_dir(parent: Path) -> tuple[Path, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    for suffix in range(100):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        final = parent / (stamp if suffix == 0 else f"{stamp}-{suffix}")
        candidate = final.with_name(f".{final.name}.building")
        try:
            candidate.mkdir()
            return candidate, final
        except FileExistsError:
            continue
    raise RuntimeError("could not allocate a fresh timestamped build directory")


def _source(source: Path, download: bool) -> None:
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists() and download:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != SOURCE_SHA256:
            raise RuntimeError("downloaded SheenChair hash does not match the pinned asset")
        source.write_bytes(payload)
    if not source.is_file():
        raise RuntimeError(f"missing {source}; rerun with --download")
    if _sha256(source) != SOURCE_SHA256:
        raise RuntimeError(f"existing {source} does not match pinned SheenChair hash")


def _manifest_files(data_dir: Path) -> tuple[dict, dict[str, str]]:
    manifest = json.loads((data_dir / MANIFEST).read_text(encoding="utf-8"))
    expected = manifest.get("sha256")
    actual = {path.relative_to(data_dir).as_posix(): _sha256(path)
              for path in sorted(data_dir.rglob("*"))
              if path.is_file() and path.name != MANIFEST}
    if expected != actual:
        raise RuntimeError(f"manifest hash mismatch in {data_dir}")
    if set(path.name for path in data_dir.iterdir()) != {"meshes", "textures", MANIFEST}:
        raise RuntimeError(f"unexpected Data root contents in {data_dir}")
    return manifest, actual


def _zip_bundle(archive: Path, data_dir: Path) -> dict[str, str]:
    entries = [(path, path.relative_to(data_dir).as_posix())
               for path in sorted(data_dir.rglob("*")) if path.is_file()]
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as output:
        for source, name in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, source.read_bytes(), compresslevel=9)
    expected = {name: _sha256(source) for source, name in entries}
    with zipfile.ZipFile(archive) as check:
        bad = check.testzip()
        if bad is not None:
            raise RuntimeError(f"zip CRC failed for {bad}")
        actual = {info.filename: hashlib.sha256(check.read(info)).hexdigest()
                  for info in check.infolist()}
    if actual != expected:
        raise RuntimeError(f"zip content hash mismatch in {archive}")
    return actual


def _source_readme(path: Path) -> None:
    path.write_text(
        "# SheenChair 驗證素材來源\n\n"
        "本批次使用 Khronos glTF Sample Assets 的固定版 SheenChair。模型署名為\n"
        "Wayfair／Eric Chadwick，授權為 CC0。轉換器使用預設材質；glTF sheen 與\n"
        "material variants 不在本次驗證範圍。\n\n"
        f"上游署名 README：{ATTRIBUTION_URL}\n\n"
        f"固定 GLB：{SOURCE_URL}\n\n"
        f"SheenChair.glb SHA-256: `{SOURCE_SHA256}`\n",
        encoding="utf-8")


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true",
                        help="download the missing pinned SheenChair source")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    home_doc = project / "HOME-VALIDATION.md"
    if not home_doc.is_file():
        raise RuntimeError(f"missing required bundle guide: {home_doc}")
    backend = project / "backend" / "home-validation"
    source = project / "backend" / "real-assets" / "SheenChair.glb"
    _source(source, args.download)
    build, final = _fresh_build_dir(backend)
    guide = build / home_doc.name
    shutil.copyfile(home_doc, guide)
    source_readme = build / "SOURCE-README.md"
    _source_readme(source_readme)
    original = read_gltf(str(source))
    report = {"schema": "model-converter-home-validation/1",
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "source": {"revision": REV, "url": SOURCE_URL,
                         "sha256": SOURCE_SHA256}, "variants": {}}
    for size in (1024, 2048):
        name = f"sheenchair_{size}"
        data_dir = build / f"Data-{name}"
        started = time.perf_counter()
        command = [sys.executable, "-m", "any2nif", str(source), str(data_dir),
                   "--package", "--collision", "convex-mesh", "--bake-size",
                   str(size), "--asset-name", name]
        proc = subprocess.run(command, cwd=project, text=True)
        if proc.returncode:
            raise RuntimeError(f"any2nif failed for {size} (exit {proc.returncode})")
        elapsed = time.perf_counter() - started
        manifest, hashes = _manifest_files(data_dir)
        nif = data_dir / manifest["mesh"]
        for ref in manifest["textures"]:
            portable_ref = ref.replace("\\", "/")
            if not portable_ref.startswith(f"textures/any2nif/{name}/"):
                raise RuntimeError(f"texture path escaped variant namespace: {ref}")
            with Image.open(data_dir / portable_ref) as image:
                if image.size != (size, size):
                    raise RuntimeError(
                        f"referenced baked DDS has size {image.size}, expected {size}x{size}: {ref}")
        result = read_nif(nif.read_bytes())
        if len(result) != len(original):
            raise RuntimeError(f"shape count mismatch for {size}")
        for before, after in zip(original, result):
            _corner_geometry(before, after, atlas=True)
        diffuse = validate_sheen_diffuse(source, data_dir, original, result)
        header = _read_header(_Reader(nif.read_bytes()))
        collisions = header["types"].count("bhkConvexVerticesShape")
        if collisions != len(original):
            raise RuntimeError(
                f"collision hull count {collisions} does not match {len(original)} source shapes")
        report["variants"][str(size)] = {
            "asset_name": name, "seconds": round(elapsed, 3),
            "data_bytes": sum(path.stat().st_size for path in data_dir.rglob("*") if path.is_file()),
            "shapes": len(result),
            "triangles": sum(len(mesh.triangles) for mesh in result),
            "textures": len(manifest["textures"]), "collision_hulls": collisions,
            "diffuse": {str(index): {"mae": mae, "p90": p90, "rgb_samples": count}
                        for index, (mae, p90, count) in sorted(diffuse.items())},
            "data_sha256": hashes}
    archive_results = {}
    for size in (1024, 2048):
        name = f"sheenchair_{size}"
        archive = build / f"{name}.zip"
        contents = _zip_bundle(archive, build / f"Data-{name}")
        archive_results[str(size)] = {
            "file": archive.name, "bytes": archive.stat().st_size,
            "sha256": _sha256(archive), "contents_sha256": contents}
    (build / "SHA256SUMS").write_text(
        "".join(f"{archive_results[str(size)]['sha256']}  sheenchair_{size}.zip\n"
                for size in (1024, 2048)), encoding="ascii")
    if _sha256(source) != SOURCE_SHA256:
        raise RuntimeError("source changed while building")
    report["archives"] = archive_results
    (build / "sha256-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    build.rename(final)
    for size in (1024, 2048):
        stats = report["variants"][str(size)]
        archive = archive_results[str(size)]
        print(f"{size}: {stats['seconds']:.1f}s, {stats['data_bytes']} Data bytes, "
              f"{stats['shapes']} shapes, {stats['triangles']} triangles, "
              f"{stats['textures']} referenced DDS, {stats['collision_hulls']} hulls, "
              f"zip {archive['bytes']} bytes")
    print(final.resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (OSError, RuntimeError, ValueError, AssertionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

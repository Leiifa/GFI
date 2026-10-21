"""Build the portable Windows release zip.

The default path downloads the pinned Gyan FFmpeg build. For a local build,
pass ``--ffmpeg-bin-dir`` to reuse an installed 9.0.2 full build.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import site
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
FFMPEG_VERSION = "9.0.2"
FFMPEG_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
    "ffmpeg-9.0.2-full_build.zip"
)
FFMPEG_SHA256 = "759d0a9831c436a0eb331ad36f236c06cb04aaa0005da46571f0e9d3d9206f6b"


def run(command: list[str], **kwargs) -> None:
    print(">", " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from inbetween import __version__

    return __version__


def fetch_models() -> None:
    run([sys.executable, str(ROOT / "scripts" / "fetch_model.py")])


def _download_ffmpeg() -> Path:
    cache = BUILD / "cache" / f"ffmpeg-{FFMPEG_VERSION}-full_build.zip"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file() or digest(cache) != FFMPEG_SHA256:
        partial = cache.with_suffix(".part")
        print(f"downloading {FFMPEG_URL}", flush=True)
        request = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": "gfi-build"})
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        if digest(partial) != FFMPEG_SHA256:
            partial.unlink(missing_ok=True)
            raise SystemExit("FFmpeg download failed the SHA-256 check")
        partial.replace(cache)
    extract = BUILD / "ffmpeg-extract"
    if extract.exists():
        shutil.rmtree(extract)
    with zipfile.ZipFile(cache) as archive:
        archive.extractall(extract)
    matches = list(extract.glob("*/bin/ffmpeg.exe"))
    if not matches:
        raise SystemExit("ffmpeg.exe was not found in the FFmpeg archive")
    return matches[0].parent


def _ffmpeg_bin_dir(override: Path | None) -> Path:
    if override is not None:
        path = override.expanduser().resolve()
        if not (path / "ffmpeg.exe").is_file() or not (path / "ffprobe.exe").is_file():
            raise SystemExit(f"ffmpeg.exe and ffprobe.exe are required in {path}")
        return path
    return _download_ffmpeg()


def stage_ffmpeg(bin_dir: Path) -> Path:
    output = BUILD / "stage" / "tools" / "ffmpeg" / "bin"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(bin_dir / name, output / name)
    version_text = subprocess.run(
        [str(bin_dir / "ffmpeg.exe"), "-version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[0]
    if f"ffmpeg version {FFMPEG_VERSION}-full_build" not in version_text:
        raise SystemExit(f"unexpected FFmpeg build: {version_text}")
    return output


def stage_licenses(ffmpeg_bin_dir: Path) -> None:
    licenses = BUILD / "stage" / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    ffmpeg_root = ffmpeg_bin_dir.parent
    for name, target in (
        ("LICENSE", "FFmpeg-LICENSE.txt"),
        ("README.txt", "FFmpeg-README.txt"),
    ):
        source = ffmpeg_root / name
        if source.is_file():
            shutil.copy2(source, licenses / target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    site_packages = None
    for candidate in site.getsitepackages():
        folder = Path(candidate)
        if (folder / "numpy").is_dir() or any(folder.glob("numpy-*.dist-info")):
            site_packages = folder
            break
    if site_packages is not None:
        numpy_licenses = next(site_packages.glob("numpy-*.dist-info/licenses"), None)
        if numpy_licenses is not None:
            shutil.copytree(numpy_licenses, licenses / "numpy", dirs_exist_ok=True)
    for license_file in (ROOT / "packaging" / "licenses").glob("*"):
        if license_file.is_file():
            shutil.copy2(license_file, licenses / license_file.name)


def write_version_file(path: Path, release: str) -> None:
    parts = tuple(int(part) for part in release.split("."))
    numbers = parts + (0,) * (4 - len(parts))
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers}, prodvers={numbers},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'GFI'),
        StringStruct('FileDescription', 'GFI - Generate Frame Inbetween'),
        StringStruct('FileVersion', '{release}'),
        StringStruct('InternalName', 'gfi'),
        StringStruct('OriginalFilename', 'gfi.exe'),
        StringStruct('ProductName', 'GFI'),
        StringStruct('ProductVersion', '{release}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path.write_text(text, encoding="ascii")


def build(release: str, ffmpeg_bin_dir: Path) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    version_file = BUILD / "version_info.txt"
    write_version_file(version_file, release)
    if not (ROOT / "assets" / "gfi.ico").is_file():
        raise SystemExit("assets/gfi.ico is missing; run scripts/make_icon.py first")

    stage_ffmpeg(ffmpeg_bin_dir)
    stage_licenses(ffmpeg_bin_dir)

    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            "gfi",
            "--icon",
            str(ROOT / "assets" / "gfi.ico"),
            "--version-file",
            str(version_file),
            "--paths",
            str(ROOT),
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD / "pyinstaller"),
            "--specpath",
            str(BUILD),
            "--hidden-import",
            "onnxruntime.capi._pybind_state",
            "--collect-binaries",
            "onnxruntime",
            "--add-data",
            f"{ROOT / 'assets'}{os.pathsep}assets",
            "--add-data",
            f"{ROOT / 'models'}{os.pathsep}models",
            "--add-data",
            f"{BUILD / 'stage' / 'tools'}{os.pathsep}tools",
            str(ROOT / "packaging" / "gfi_launcher.py"),
        ]
    )

    app_dir = DIST / "gfi"
    release_dir = DIST / f"GFI-{release}"
    if release_dir.exists():
        shutil.rmtree(release_dir)
    app_dir.rename(release_dir)

    shutil.copy2(ROOT / "LICENSE", release_dir / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", release_dir / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(
        ROOT / "packaging" / "PORTABLE_README.txt",
        release_dir / "README.txt",
    )
    shutil.copytree(BUILD / "stage" / "licenses", release_dir / "licenses")

    required = [
        release_dir / "gfi.exe",
        release_dir / "_internal" / "assets" / "gfi-logo.png",
        release_dir / "_internal" / "models" / "RIFE_fp32_timestep.onnx",
        release_dir / "_internal" / "models" / "RIFE_4.9.onnx",
        release_dir / "_internal" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        release_dir / "_internal" / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("build is incomplete:\n" + "\n".join(missing))

    archive = shutil.make_archive(
        str(DIST / f"GFI-{release}-win64"),
        "zip",
        root_dir=DIST,
        base_dir=f"GFI-{release}",
    )
    archive_path = Path(archive)
    checksum = digest(archive_path)
    (DIST / "SHA256SUMS").write_text(f"{checksum}  {archive_path.name}\n", encoding="ascii")
    print(f"\nBuilt {archive_path} ({archive_path.stat().st_size / 1e6:.1f} MB)")
    print(f"SHA-256 {checksum}")
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="release version (default: package version)")
    parser.add_argument(
        "--ffmpeg-bin-dir",
        type=Path,
        default=None,
        help="use an installed FFmpeg 9.0.2 full-build bin folder instead of downloading",
    )
    args = parser.parse_args()
    release = args.version or version()
    if not (ROOT / "THIRD_PARTY_NOTICES.md").is_file():
        raise SystemExit("THIRD_PARTY_NOTICES.md is missing")
    fetch_models()
    build(release, _ffmpeg_bin_dir(args.ffmpeg_bin_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

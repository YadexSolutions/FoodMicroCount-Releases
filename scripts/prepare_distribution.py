"""Stage the Windows payload and bind it to a Git source archive."""
from importlib import metadata
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foodmicro import __version__

NOTICE_PATTERN = re.compile(r"licen[cs]e|copying|copyright|notice", re.I)
IGNORED_ROOTS = {".git", ".venv", ".pytest_cache", "build", "dist"}
DOCS = ("BUILD_WINDOWS.md", "RELEASE_NOTES.md", "VALIDATION.md",
        "FoodMicroCount-1.0-Beginners-Guide.pdf")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_source(archive):
    """Return the commit stamped by git archive and require the source bytes."""
    with ZipFile(archive) as source:
        revision = source.comment.decode("ascii")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Use the source ZIP generated from the release Git commit")
        entries = [PurePosixPath(item.filename) for item in source.infolist() if not item.is_dir()]
        if not entries or len({p.parts[0] for p in entries}) != 1:
            raise ValueError("Source ZIP must have exactly one top-level directory")
        names = set()
        for entry in entries:
            if len(entry.parts) < 2 or entry.is_absolute() or ".." in entry.parts:
                raise ValueError(f"Invalid archive entry {entry}")
            relative = PurePosixPath(*entry.parts[1:])
            names.add(relative.as_posix())
            path = ROOT / relative
            if not path.is_file() or source.read(entry.as_posix()) != path.read_bytes():
                raise ValueError(f"Source ZIP differs from the build directory at {relative}")
        # A generated PyInstaller spec and Python caches are local outputs.
        for path in ROOT.rglob("*"):
            relative = path.relative_to(ROOT)
            if (not path.is_file() or relative.parts[0] in IGNORED_ROOTS
                or "__pycache__" in relative.parts or relative.parts[0].endswith(".egg-info")
                or relative.as_posix() == "FoodMicroCount.spec"):
                continue
            if relative.as_posix() not in names:
                raise ValueError(f"Build directory has a file outside the source archive: {relative}")
    return revision


def prepare(bundle, input_source=None):
    archive = bundle.parent / f"FoodMicroCount-{__version__}-Source.zip"
    if input_source:
        source = input_source.resolve()
        revision = verify_source(source)
        if source != archive.resolve():
            shutil.copy2(source, archive)
    else:
        if not (ROOT / ".git").exists():
            raise ValueError("Build from a clean Git checkout or supply -SourceArchive")
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True
        )
        if status.strip():
            raise ValueError("The build checkout has uncommitted changes")
        subprocess.run(
            ["git", "archive", "--format=zip", "--prefix=FoodMicroCount/", "-o", str(archive), "HEAD"],
            cwd=ROOT, check=True
        )
        revision = verify_source(archive)

    # The desktop UI uses raster widgets and still images only. These optional
    # wheel plugins are not required by either the image or calculation pipeline.
    for plugin in (bundle / "_internal" / "cv2").glob("opencv_videoio_ffmpeg*.dll"):
        plugin.unlink()
    (bundle / "_internal/PySide6/opengl32sw.dll").unlink(missing_ok=True)

    (bundle / "demo").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "demo/synthetic_plates.png", bundle / "demo")
    (bundle / "docs").mkdir(exist_ok=True)
    for name in DOCS:
        shutil.copy2(ROOT / "docs" / name, bundle / "docs")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "WINDOWS_READ_ME_FIRST.txt", "WINDOWS_FIRST_RUN.txt"):
        shutil.copy2(ROOT / name, bundle)

    notices = bundle / "Third-party licences"
    (notices / "CPython").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "licences", notices, dirs_exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", notices / "CPython")
    versions = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if name.lower().replace("_", "-") == "foodmicro-count":
            continue
        versions[name] = distribution.version
        for file in distribution.files or ():
            if ".." in file.parts or ("licenses" not in file.parts and not NOTICE_PATTERN.search(file.name)):
                continue
            if not (any(".dist-info" in part for part in file.parts) or file.parts[0] in {"cv2", "PyInstaller"}):
                continue
            source = distribution.locate_file(file)
            if source.is_file():
                target = notices / name / Path(*file.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    (notices / "BUILD_ENVIRONMENT.txt").write_text(
        f"FoodMicro Count=={__version__}\nCPython=={sys.version.split()[0]}\n"
        + "".join(f"{key}=={versions[key]}\n" for key in sorted(versions, key=str.lower)),
        encoding="utf-8",
    )
    shutil.copy2(archive, bundle)
    manifest = {
        "application_version": __version__,
        "release_tag": "v1.0.0-rc.1",
        "source_git_commit": revision,
        "source_archive": archive.name,
        "source_archive_sha256": digest(archive),
        "frozen_executable_sha256": digest(bundle / "FoodMicroCount.exe"),
        "beginner_guide_sha256": digest(bundle / "docs/FoodMicroCount-1.0-Beginners-Guide.pdf"),
        "python_version": sys.version.split()[0],
        "windows_setup_compiler": "Inno Setup 6.7.4",
        "python_build_environment": versions,
    }
    for directory in (bundle, bundle.parent):
        (directory / "BUILD_PROVENANCE.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    prepare(args.bundle.resolve(), args.source_archive)

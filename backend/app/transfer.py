"""Media import (upload / server-folder copy) and export (zip / folder copy).

Everything here streams in fixed-size chunks so multi-GB drone footage never
sits in RAM. Imports always land under a MEDIA_DIRS root so the scanner and
folder watcher accept them.
"""
from __future__ import annotations

import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

from . import config, persist

CHUNK = 8 * 1024 * 1024
# Free-space headroom kept on the destination after a copy/upload.
SPACE_MARGIN = 256 * 1024 * 1024

# Companion files that travel with media (telemetry, raw stills, subtitles...).
SIDECAR_EXTS = {
    ".srt", ".vtt", ".ass", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf",
    ".rw2", ".lrf", ".thm", ".xmp", ".aae", ".xml", ".json", ".txt", ".htm", ".html",
    ".gpx", ".kml", ".wav", ".mp3", ".aac", ".m4a", ".csv", ".log", ".dat",
}
UPLOAD_EXTS = config.SUPPORTED_EXTS | SIDECAR_EXTS


class TransferError(Exception):
    """User-facing validation failure; ``status`` is the HTTP status to use."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


ProgressFn = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# paths / names
# ---------------------------------------------------------------------------
def media_roots() -> list[Path]:
    roots = []
    for d in persist.media_dirs():
        try:
            roots.append(Path(d).expanduser().resolve())
        except OSError:
            continue
    return roots


def library_root() -> Path:
    """Directory imports are written under (first media root)."""
    override = os.environ.get("MF_IMPORT_ROOT", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        roots = media_roots()
        if not roots:
            raise TransferError("no media directory is configured", 409)
        root = roots[0]
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def in_media_root(path: Path) -> bool:
    return any(is_within(path, r) for r in media_roots())


def safe_name(raw: str, fallback: str = "file") -> str:
    name = os.path.basename((raw or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f/]", "", name).strip().lstrip(".")
    return name[:200] or fallback


def unique_path(directory: Path, name: str) -> Path:
    """directory/name, or 'stem (n).ext' if taken (incl. in-flight .part)."""
    stem, ext = os.path.splitext(name)
    cand = directory / name
    n = 1
    while cand.exists() or Path(str(cand) + ".part").exists():
        cand = directory / f"{stem} ({n}){ext}"
        n += 1
    return cand


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def ensure_space(dest: Path, needed: int) -> None:
    free = free_bytes(dest)
    if free < needed + SPACE_MARGIN:
        raise TransferError(
            f"not enough free space on destination: need {needed / 1e9:.2f} GB "
            f"(+{SPACE_MARGIN // (1024 * 1024)} MB margin), {free / 1e9:.2f} GB free",
            507,
        )


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------
def upload_dir() -> Path:
    d = library_root() / "imports" / datetime.now().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare_upload(filename: str, content_length: Optional[int]) -> tuple[Path, Path]:
    """Validate an upload and return (final_path, part_path) to stream into."""
    name = safe_name(filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in UPLOAD_EXTS:
        raise TransferError(f"unsupported file type {ext or '(none)'}", 415)
    directory = upload_dir()
    if content_length:
        ensure_space(directory, content_length)
    final = unique_path(directory, name)
    return final, Path(str(final) + ".part")


def commit_upload(part: Path, final: Path) -> Path:
    """Move .part into place without clobbering an existing file."""
    if final.exists():  # appeared meanwhile
        final = unique_path(final.parent, final.name)
    os.rename(part, final)
    return final


# ---------------------------------------------------------------------------
# folder import
# ---------------------------------------------------------------------------
def _walk_files(root: Path) -> tuple[list[tuple[Path, int]], int]:
    """Eligible (path, size) files plus a count of skipped ones.

    Only upload-allowed types (media + sidecars) are kept; hidden files and
    hidden directories (dotfiles like .ssh) and symlinks are skipped.
    """
    out: list[tuple[Path, int]] = []
    skipped = 0
    for cur, dirs, names in os.walk(root, followlinks=False):
        kept = []
        for d in dirs:
            if d.startswith("."):
                skipped += sum(len(f) for _r, _d, f in os.walk(Path(cur) / d))
            else:
                kept.append(d)
        dirs[:] = kept
        for n in names:
            p = Path(cur) / n
            if (n.startswith(".") or p.is_symlink() or not p.is_file()
                    or os.path.splitext(n)[1].lower() not in UPLOAD_EXTS):
                skipped += 1
                continue
            try:
                out.append((p, p.stat().st_size))
            except OSError:
                skipped += 1
    out.sort(key=lambda t: str(t[0]))
    return out, skipped


def plan_folder_import(raw: str) -> dict:
    """Validate a server path and return the copy plan (no I/O on the dest)."""
    text = (raw or "").strip()
    if not text:
        raise TransferError("path must not be empty")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise TransferError("path must be absolute")
    try:
        src = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise TransferError(f"path does not exist: {text}", 404)
    if not src.is_dir():
        raise TransferError(f"not a directory: {src}")
    if src == Path(src.anchor):
        raise TransferError("refusing to import the filesystem root")
    root = library_root()
    for r in media_roots() + [root]:
        if is_within(src, r):
            raise TransferError(
                f"{src} is already inside the library ({r}); scan it instead of importing")
    if is_within(root, src):
        raise TransferError("source contains the library; refusing to copy it onto itself")
    files, skipped = _walk_files(src)
    if not files:
        raise TransferError(
            f"folder contains no importable media or companion files ({skipped} skipped)")
    total = sum(s for _p, s in files)
    dest = unique_path(root, safe_name(src.name, "import"))
    ensure_space(root, total)
    return {"src": src, "dest": dest, "files": files, "bytes": total, "skipped": skipped}


def copy_file(src: Path, dst: Path, on_bytes: Optional[Callable[[int], None]] = None,
              cancelled: Optional[Callable[[], bool]] = None) -> None:
    """Chunked copy via a .part file; preserves mtime. Never overwrites."""
    part = Path(str(dst) + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(src, "rb") as fin, open(part, "wb") as fout:
            while True:
                if cancelled and cancelled():
                    raise InterruptedError("cancelled")
                buf = fin.read(CHUNK)
                if not buf:
                    break
                fout.write(buf)
                if on_bytes:
                    on_bytes(len(buf))
        try:
            shutil.copystat(src, part)
        except OSError:
            pass
        commit_upload(part, dst)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise


def copy_tree(files: list[tuple[Path, int]], src_root: Path, dest_root: Path,
              progress: Optional[ProgressFn] = None,
              cancelled: Optional[Callable[[], bool]] = None,
              frac_scale: float = 1.0) -> int:
    total = sum(s for _p, s in files) or 1
    state = {"done": 0, "last": 0.0}

    def on_bytes(n: int) -> None:
        state["done"] += n
        now = time.monotonic()
        if progress and now - state["last"] > 0.5:
            state["last"] = now
            progress(state["done"] / total * frac_scale,
                     f"copying {state['done'] / 1e9:.2f}/{total / 1e9:.2f} GB")

    for i, (p, _size) in enumerate(files):
        rel = p.relative_to(src_root)
        copy_file(p, dest_root / rel, on_bytes, cancelled)
        if progress:
            progress(state["done"] / total * frac_scale,
                     f"copied {i + 1}/{len(files)}: {p.name}")
    return state["done"]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def sidecars_for(path: str, listing: Optional[dict[str, list[str]]] = None) -> list[str]:
    """Files next to ``path`` sharing its basename (case-insensitive stem),
    excluding scanner-supported media (those are separate assets)."""
    p = Path(path)
    d = str(p.parent)
    if listing is None:
        listing = {}
    if d not in listing:
        try:
            listing[d] = sorted(os.listdir(d))
        except OSError:
            listing[d] = []
    stem = p.stem.lower()
    out = []
    for n in listing[d]:
        if n == p.name or n.endswith(".part"):
            continue
        s, ext = os.path.splitext(n)
        if s.lower() != stem:
            continue
        if ext.lower() in config.SUPPORTED_EXTS:
            continue
        full = os.path.join(d, n)
        if os.path.isfile(full):
            out.append(full)
    return out


def export_file_list(paths: list[str], include_sidecars: bool = True) -> list[tuple[str, str]]:
    """[(source_path, archive_name)] for the selected asset paths, de-duped."""
    listing: dict[str, list[str]] = {}
    seen: set[str] = set()
    names: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(src: str) -> None:
        if src in seen or not os.path.isfile(src):
            return
        seen.add(src)
        base = os.path.basename(src)
        stem, ext = os.path.splitext(base)
        name, n = base, 1
        while name.lower() in names:
            name = f"{stem} ({n}){ext}"
            n += 1
        names.add(name.lower())
        out.append((src, name))

    for p in paths:
        add(p)
        if include_sidecars:
            for sc in sidecars_for(p, listing):
                add(sc)
    return out


class _Sink:
    """Write-only, non-seekable buffer so zipfile emits data descriptors."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._pos = 0

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        self._pos += len(data)
        return len(data)

    def flush(self) -> None:
        pass

    def tell(self) -> int:
        return self._pos

    def seek(self, *_a, **_k):
        raise OSError("unseekable")

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def stream_zip(files: list[tuple[str, str]]) -> Iterator[bytes]:
    """Yield a ZIP_STORED (uncompressed) archive piece by piece."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for src, arcname in files:
            try:
                st = os.stat(src)
                fin = open(src, "rb")
            except OSError:
                continue  # vanished mid-export
            with fin:
                tm = time.localtime(max(st.st_mtime, 315532800))  # zip epoch 1980
                zi = zipfile.ZipInfo(arcname, date_time=tm[:6])
                zi.compress_type = zipfile.ZIP_STORED
                zi.external_attr = (st.st_mode & 0xFFFF) << 16
                with zf.open(zi, "w", force_zip64=True) as dest:
                    while True:
                        buf = fin.read(CHUNK)
                        if not buf:
                            break
                        dest.write(buf)
                        data = sink.drain()
                        if data:
                            yield data
            data = sink.drain()
            if data:
                yield data
    data = sink.drain()
    if data:
        yield data


def default_export_dir() -> Path:
    return config.EXPORTS_DIR / "media" / datetime.now().strftime("%Y%m%d-%H%M%S")


def plan_folder_export(paths: list[str], dest_raw: Optional[str],
                       include_sidecars: bool = True) -> dict:
    files = export_file_list(paths, include_sidecars)
    if not files:
        raise TransferError("none of the selected files exist on disk", 404)
    text = (dest_raw or "").strip()
    dest = Path(text).expanduser() if text else default_export_dir()
    if not dest.is_absolute() or ".." in dest.parts:
        raise TransferError("destination must be an absolute path without '..'")
    dest = dest.resolve()
    if dest.exists() and not dest.is_dir():
        raise TransferError(f"destination is not a directory: {dest}")
    if in_media_root(dest) or any(is_within(r, dest) for r in media_roots()):
        raise TransferError("destination must be outside the media library")
    total = sum(os.path.getsize(s) for s, _n in files)
    ensure_space(dest, total)
    return {"dest": dest, "files": files, "bytes": total}


def copy_export(files: list[tuple[str, str]], dest: Path,
                progress: Optional[ProgressFn] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    total = sum(os.path.getsize(s) for s, _n in files) or 1
    state = {"done": 0, "last": 0.0}

    def on_bytes(n: int) -> None:
        state["done"] += n
        now = time.monotonic()
        if progress and now - state["last"] > 0.5:
            state["last"] = now
            progress(state["done"] / total,
                     f"exporting {state['done'] / 1e9:.2f}/{total / 1e9:.2f} GB")

    for i, (src, name) in enumerate(files):
        copy_file(Path(src), unique_path(dest, name), on_bytes, cancelled)
        if progress:
            progress(state["done"] / total, f"exported {i + 1}/{len(files)}: {name}")
    return len(files)

"""Approval-gated removable-card imports with descriptor-safe source reads."""
from __future__ import annotations

import hashlib
import json
import os
import stat as statmod
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import config, transfer
from .scanqueue import scan_queue


class RemovableImportError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message); self.status = status


class RemovableImportService:
    def __init__(self, roots: list[Path] | None = None) -> None:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        self.roots = roots or [Path("/media") / user, Path("/run/media") / user]
        self._pending: dict[str, dict[str, Any]] = {}
        self._dismissed: set[str] = set(); self._reserved: set[str] = set()
        self._lock = threading.RLock(); self._stop = threading.Event(); self._thread: threading.Thread | None = None

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        stat = path.stat(follow_symlinks=False); return stat.st_dev, stat.st_ino

    def _manifest(self, dcim: Path, mount: Path) -> tuple[tuple[int, int], list[dict[str, Any]]]:
        """List regular supported files without resolving or following links."""
        identity = self._identity(mount)
        if dcim.is_symlink() or not dcim.is_dir(): raise OSError("DCIM disappeared")
        result: list[dict[str, Any]] = []
        for current, dirs, names in os.walk(dcim, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in names:
                path = Path(current) / name
                if path.suffix.lower() not in config.SUPPORTED_EXTS or path.is_symlink(): continue
                try: stat = path.stat(follow_symlinks=False)
                except OSError: continue
                if not statmod.S_ISREG(stat.st_mode): continue
                result.append({"relative": path.relative_to(dcim).as_posix(), "size": stat.st_size,
                               "mtime_ns": stat.st_mtime_ns, "dev": stat.st_dev, "ino": stat.st_ino,
                               "ext": path.suffix.lower()})
        return identity, sorted(result, key=lambda item: item["relative"])

    def poll(self) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for root in self.roots:
            try: mounts = list(root.iterdir()) if root.is_dir() else []
            except OSError: continue
            for mount in mounts:
                try:
                    if mount.is_symlink() or not mount.is_dir(): continue
                    dcim = mount / "DCIM"; identity, manifest = self._manifest(dcim, mount)
                    if not manifest: continue
                    manifest_digest = hashlib.sha256(json.dumps(manifest, sort_keys=True,
                                                               separators=(",", ":")).encode()).hexdigest()
                    token = hashlib.sha256(f"{identity[0]}:{identity[1]}:{mount}:{manifest_digest}".encode()).hexdigest()[:16]
                    found[token] = {"id": token, "source": str(mount), "dcim": str(dcim), "identity": identity,
                                    "manifest": manifest, "file_count": len(manifest),
                                    "size": sum(entry["size"] for entry in manifest),
                                    "types": sorted({entry["ext"].lstrip(".") for entry in manifest})}
                except OSError: continue
        with self._lock:
            present = set(found); self._dismissed.intersection_update(present); self._reserved.intersection_update(present)
            self._pending = {key: item for key, item in found.items() if key not in self._dismissed and key not in self._reserved}
            return [self._public(item) for item in self._pending.values()]

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in ("id", "source", "file_count", "size", "types")}

    def dismiss(self, import_id: str) -> bool:
        with self._lock:
            if self._pending.pop(import_id, None) is None: return False
            self._dismissed.add(import_id); return True

    def reserve(self, import_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._pending.pop(import_id, None)
            if item is not None: self._reserved.add(import_id)
        if item is None: raise RemovableImportError("pending import not found", 404)
        try:
            mount, dcim = Path(item["source"]), Path(item["dcim"])
            identity, manifest = self._manifest(dcim, mount)
            if identity != item["identity"] or manifest != item["manifest"]: raise OSError("card contents changed")
            total = sum(entry["size"] for entry in manifest)
            dest = transfer.library_root() / "imports" / datetime.now().strftime("%Y-%m-%d") / mount.name
            transfer.ensure_space(dest, total)
            return {"source": str(mount), "mount": mount, "dcim": dcim, "identity": identity,
                    "manifest": manifest, "bytes": total, "dest": dest}
        except (OSError, transfer.TransferError) as exc:
            with self._lock: self._reserved.discard(import_id)
            if isinstance(exc, transfer.TransferError): raise RemovableImportError(str(exc), exc.status) from exc
            raise RemovableImportError("removable media changed; approve it again", 409) from exc

    @staticmethod
    def _open_file(mount: Path, identity: tuple[int, int], entry: dict[str, Any]) -> int:
        """Open card path components and file with O_NOFOLLOW, then verify fstat."""
        nofollow = getattr(os, "O_NOFOLLOW", 0); directory = getattr(os, "O_DIRECTORY", 0)
        fds = [os.open(mount, os.O_RDONLY | directory | nofollow)]
        try:
            root_stat = os.fstat(fds[0])
            if (root_stat.st_dev, root_stat.st_ino) != identity: raise OSError("card mount changed")
            for index, part in enumerate(["DCIM", *Path(entry["relative"]).parts]):
                if part in ("", ".", ".."): raise OSError("unsafe card path")
                flags = os.O_RDONLY | nofollow | (directory if index < len(Path(entry["relative"]).parts) else 0)
                fds.append(os.open(part, flags, dir_fd=fds[-1]))
            stat = os.fstat(fds[-1])
            expected = (entry["size"], entry["mtime_ns"], entry["dev"], entry["ino"])
            if (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino) != expected: raise OSError("card file changed")
            for fd in fds[:-1]: os.close(fd)
            return fds[-1]
        except BaseException:
            for fd in reversed(fds):
                try: os.close(fd)
                except OSError: pass
            raise

    @staticmethod
    def _hash_fd(fd: int) -> str:
        digest = hashlib.sha256(); os.lseek(fd, 0, os.SEEK_SET)
        while block := os.read(fd, transfer.CHUNK): digest.update(block)
        os.lseek(fd, 0, os.SEEK_SET); return digest.hexdigest()

    @staticmethod
    def _duplicate_index(library: Path) -> dict[int, set[str]]:
        index: dict[int, set[str]] = {}
        for path in library.rglob("*") if library.exists() else ():
            if path.is_symlink() or not path.is_file(): continue
            try:
                size = path.stat().st_size
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                index.setdefault(size, set()).add(digest)
            except OSError: continue
        return index

    @staticmethod
    def _copy_fd(fd: int, dest: Path, stat: os.stat_result, copied: int, total: int,
                 progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> None:
        part = Path(str(dest) + ".part"); part.parent.mkdir(parents=True, exist_ok=True)
        try:
            with part.open("xb") as out:
                while block := os.read(fd, transfer.CHUNK):
                    if cancelled(): raise InterruptedError("cancelled")
                    out.write(block); copied += len(block); progress(min(.94, copied / total), "copying removable media")
                out.flush(); os.fsync(out.fileno())
            os.utime(part, ns=(stat.st_atime_ns, stat.st_mtime_ns)); transfer.commit_upload(part, dest)
        except BaseException:
            part.unlink(missing_ok=True); raise

    def copy(self, plan: dict[str, Any], progress: Callable[[float, str], None], cancelled: Callable[[], bool]) -> None:
        try:
            if self._identity(plan["mount"]) != plan["identity"]: raise OSError("card changed")
            copied, total = 0, max(1, int(plan["bytes"])); duplicates = self._duplicate_index(transfer.library_root())
            for entry in plan["manifest"]:
                if cancelled(): return
                fd = self._open_file(plan["mount"], plan["identity"], entry)
                try:
                    digest = self._hash_fd(fd)
                    if digest in duplicates.get(entry["size"], set()):
                        copied += entry["size"]; progress(min(.94, copied / total), "skipping duplicate removable media"); continue
                    relative = Path(entry["relative"])
                    dest = transfer.unique_path(plan["dest"] / relative.parent, transfer.safe_name(relative.name))
                    self._copy_fd(fd, dest, os.fstat(fd), copied, total, progress, cancelled)
                    copied += entry["size"]; duplicates.setdefault(entry["size"], set()).add(digest)
                finally: os.close(fd)
            progress(.96, "copy complete, starting scan"); scan_queue.enqueue_threadsafe([str(plan["dest"])], source="removable-import"); progress(1.0, "removable import complete")
        except (OSError, ValueError) as exc:
            raise RemovableImportError("removable media was detached or changed", 409) from exc

    def start(self, interval: float = 5.0) -> None:
        if os.environ.get("MF_REMOVABLE_WATCHER", "1") != "1" or (self._thread and self._thread.is_alive()): return
        self._stop.clear(); self._thread = threading.Thread(target=self._loop, args=(interval,), daemon=True); self._thread.start()
    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try: self.poll()
            except Exception: pass
    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=2); self._thread = None


removable_imports = RemovableImportService()

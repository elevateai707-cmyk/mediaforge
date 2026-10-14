"""One GPU job at a time on a 10 GB card, with VRAM logged around every job.

* `vram()` reads nvidia-smi (None on a machine without an NVIDIA GPU).
* `gpu_job(name)` takes an exclusive file lock (data/locks/gpu.lock), so two
  AdLab commands, or AdLab and a cron job, never load models at the same time,
  and appends {before, after} VRAM to data/logs/gpu.jsonl.
* `run_ephemeral(argv)` runs a GPU task (Whisper, a local vision model) in a
  subprocess under that lock. When it exits, its VRAM is back to the driver: AdLab
  never keeps PyTorch models resident in its own process.

Media Forge / ComfyUI hold their own models; free them first with
`MediaForge.release_gpu()` (see docs/mediaforge_interface.md).
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config import settings


def vram() -> Optional[dict[str, int]]:
    """{'used_mb', 'free_mb', 'total_mb'} for GPU 0, or None without nvidia-smi."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()[0]
        used, free, total = (int(x.strip()) for x in out.split(","))
        return {"used_mb": used, "free_mb": free, "total_mb": total}
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return None


def _log(record: dict) -> None:
    logs = settings().logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "gpu.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


@contextmanager
def gpu_job(name: str, min_free_mb: int = 0, wait_s: float = 3600) -> Iterator[dict]:
    """Exclusive GPU section. Yields a record you can add notes to; it is logged on exit."""
    locks = settings().locks_dir
    locks.mkdir(parents=True, exist_ok=True)
    record: dict = {"job": name, "pid": os.getpid(),
                    "queued_at": datetime.now(timezone.utc).isoformat()}
    with (locks / "gpu.lock").open("w") as lock:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"GPU busy for {wait_s:.0f}s; {name} not started")
                time.sleep(1)
        record["started_at"] = datetime.now(timezone.utc).isoformat()
        record["vram_before"] = vram()
        before = record["vram_before"]
        if min_free_mb and before and before["free_mb"] < min_free_mb:
            record["error"] = f"only {before['free_mb']} MB free, need {min_free_mb}"
            _log(record)
            raise RuntimeError(f"{name}: {record['error']} (free Media Forge/ComfyUI VRAM first)")
        t0 = time.monotonic()
        try:
            yield record
            record["ok"] = True
        except BaseException as exc:
            record["ok"] = False
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["seconds"] = round(time.monotonic() - t0, 2)
            record["vram_after"] = vram()
            _log(record)
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_ephemeral(argv: list[str], name: str, timeout_s: float = 3600, min_free_mb: int = 0,
                  cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run a GPU task in its own process under the GPU lock; VRAM is freed when it exits."""
    with gpu_job(name, min_free_mb=min_free_mb) as rec:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s,
                              cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL)
        rec["returncode"] = proc.returncode
        return proc

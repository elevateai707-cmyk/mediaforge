"""Phase 1 smoke test: Media Forge reachable, VRAM freed and logged, one 9:16 image made.

    adlab mf smoke

1. Ask Media Forge for status (skipped with a warning if it isn't running).
2. Inside the GPU lock: log VRAM (nvidia-smi), ask Media Forge to release its
   models, render a 9:16 test card with Elevate Vision Studio, log VRAM again.
3. Write data/logs/smoke-<time>.json and return it.

The test card is a text still, not an AI-generated image: Media Forge has no
image generator. Once you tell me where ComfyUI lives, this smoke test also
renders one generated 9:16 image through it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from . import gpu
from .config import settings
from .mediaforge import MediaForge, MediaForgeError, png_size, render_still


def run(ratio: str = "9:16", mf: Optional[MediaForge] = None) -> dict[str, Any]:
    s = settings()
    s.ensure_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {"at": stamp, "ratio": ratio, "warnings": []}
    mf = mf or MediaForge()

    try:
        status = mf.status()
        report["mediaforge"] = {"url": mf.base_url, "gpu": status["health"].get("gpu"),
                                "studio": status["studio"].get("available"),
                                "photocraft": status["photocraft"].get("available")}
    except MediaForgeError as exc:
        report["mediaforge"] = {"url": mf.base_url, "error": str(exc)}
        report["warnings"].append("Media Forge not reachable: VRAM release skipped (start ./scripts/run.sh)")

    with gpu.gpu_job("mediaforge.smoke") as rec:
        report["vram_before"] = rec["vram_before"]
        if "error" not in report["mediaforge"]:
            try:
                report["release"] = mf.release_gpu()
            except MediaForgeError as exc:
                report["warnings"].append(f"VRAM release failed: {exc}")
        report["vram_after_release"] = gpu.vram()
        out = s.batches_dir / "smoke" / f"smoke-{stamp}-{ratio.replace(':', 'x')}.png"
        render_still(["AdLab smoke test", f"{ratio} · {stamp}"], ratio, out)  # type: ignore[arg-type]
        report["image"] = {"path": str(out), "size": list(png_size(out))}
    report["vram_after"] = gpu.vram()
    if report["vram_before"] is None:
        report["warnings"].append("no NVIDIA GPU visible (nvidia-smi missing): VRAM not measured")
    (s.logs_dir / f"smoke-{stamp}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report

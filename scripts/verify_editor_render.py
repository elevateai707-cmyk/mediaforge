#!/usr/bin/env python3
"""Reproducible offline four-state render samples; never touches library media or DB.

Run: PYTHONPATH=backend backend/.venv/bin/python scripts/verify_editor_render.py
"""

import json
import subprocess
import uuid
from pathlib import Path

from app import config
from app.edits.project import Clip, Cue, Overlay, Project, Style
from app.edits.render_v2 import render_snapshot
from app.jobs import _CancelFlag
from app.proc import tool_env


def main():
    root = config.EXPORTS_DIR / "editor-v2-samples" / uuid.uuid4().hex[:10]
    root.mkdir(parents=True)
    config.EXPORTS_DIR = root
    source = root / "synthetic-source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x203040:s=640x360:r=24:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        env=tool_env(),
    )
    clip = Clip(asset_id=1, start=0.25, end=2.25, transition="cut")
    project = Project(
        clips=[clip],
        captions={
            clip.uid: [
                Cue(start=0.25, end=2, text="Caption fixture: café, it's 100% local.")
            ]
        },
        overlays={
            clip.uid: [
                Overlay(
                    start=0.25,
                    end=2,
                    text="SEPARATE TITLE",
                    style=Style(position="top"),
                )
            ]
        },
    )
    project.export.width = 640
    project.export.ratio = "16:9"
    results = {}
    for speech, overlay in [(False, False), (True, False), (False, True), (True, True)]:
        name = f"speech-{int(speech)}_overlay-{int(overlay)}"
        project.speech_captions, project.on_video_text = speech, overlay
        output = render_snapshot(
            project.model_dump(),
            {1: {"path": str(source), "kind": "video"}},
            lambda *_: None,
            _CancelFlag(),
        )
        target = root / f"{name}.mp4"
        Path(output["video"]).rename(target)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "0.5",
                "-i",
                str(target),
                "-frames:v",
                "1",
                str(root / f"{name}.png"),
            ],
            check=True,
            env=tool_env(),
        )
        results[name] = str(target)
    (root / "manifest.json").write_text(json.dumps(results, indent=2))
    print(root)


if __name__ == "__main__":
    main()

"""Planner must not mix Vancouver clips into an Edmonton intent."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.db import SessionLocal
from app.ingest.place import assign_place
from app.models import Asset
from app.proc import tool_env

_ROOT = Path("/tmp/mf_planner")


def _tiny_video(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, env=tool_env(),
    )


def test_edmonton_plan_excludes_vancouver(client):
    specs = [
        (_ROOT / "Edmonton GPS" / "clip_edm_gps.mov", "blue", 53.5461, -113.4938),
        (_ROOT / "Vancouver" / "clip_yvr.mov", "red", 49.2827, -123.1207),
        (_ROOT / "Edmonton Trip" / "clip_edm_folder.mov", "green", None, None),
    ]
    for path, color, _lat, _lon in specs:
        _tiny_video(path, color)

    with SessionLocal() as db:
        for path, _color, lat, lon in specs:
            sp = str(path)
            row = db.query(Asset).filter_by(path=sp).first()
            if row is None:
                row = Asset(
                    path=sp,
                    hash=sp,
                    kind="video",
                    duration=2.0,
                    taken_at=datetime(2026, 7, 12, 12, tzinfo=timezone.utc),
                    gps_lat=lat,
                    gps_lon=lon,
                    status="pending",
                    aesthetic_score=7.0,
                )
                db.add(row)
                db.flush()
            else:
                row.gps_lat = lat
                row.gps_lon = lon
                row.kind = "video"
                row.duration = 2.0
            assign_place(row)
        db.commit()
        edm_ids = {
            a.id for a in db.query(Asset).all()
            if a.city == "Edmonton" or "edmonton" in (a.path or "").lower()
        }
        yvr_ids = {
            a.id for a in db.query(Asset).all()
            if a.city == "Vancouver" or "vancouver" in (a.path or "").lower()
        }

    r = client.post(
        "/api/edits/plan",
        json={"intent": "make a highlight reel for tiktok of my trip to edmonton 9:16"},
    )
    assert r.status_code == 200
    body = r.json()
    parsed = body.get("parsed_intent") or {}
    assert parsed.get("place") == "Edmonton"
    assert parsed.get("ratio") == "9:16"
    assert parsed.get("platform") == "tiktok"
    assert float(parsed.get("duration_s") or 0) == 30
    clip_ids = [c["asset_id"] for c in body["clips"]]
    assert clip_ids, body
    assert all(cid in edm_ids for cid in clip_ids), clip_ids
    assert not any(cid in yvr_ids for cid in clip_ids), clip_ids

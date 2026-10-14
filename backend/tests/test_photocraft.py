"""PhotoCraft bridge: allow-list, request validation, and the apply job.

A fake ``photocraft-cli`` (a tiny Python script) stands in for the real binary
so these run anywhere; ``test_real_cli_end_to_end`` also exercises a real
install when one is on PATH or in MF_PHOTOCRAFT_CLI_REAL.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from app import photocraft

FAKE_CLI = r'''#!{python}
import json, shutil, sys
args = sys.argv[1:]
with open({log!r}, "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:1] == ["--version"]:
    print("photocraft-cli 0.2.0 (fake)")
elif args[:1] == ["commands"]:
    print(json.dumps([
        {{"id": "image.autoTone", "label": "Auto Tone", "params": "{{}}"}},
        {{"id": "image.adjustments.vibrance", "label": "Vibrance", "params": "{{vibrance, saturation}}"}},
        {{"id": "file.save", "label": "Save", "params": "{{path}}"}},
        {{"id": "image.adjustments.colorLookup", "label": "Color Lookup", "params": "{{path}}"}},
    ]))
elif args[:1] == ["run"]:
    if "boom" in " ".join(args):
        sys.stderr.write("error: engine exploded\n"); sys.exit(1)
    shutil.copyfile(args[1], args[args.index("--out") + 1])
'''


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    cli = tmp_path / "photocraft-cli"
    cli.write_text(FAKE_CLI.format(python=sys.executable, log=str(log)))
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MF_PHOTOCRAFT_CLI", str(cli))
    return log


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def _photo_id(client, wait_job, media_dir) -> int:
    """Scan one photo with unique pixels (scans dedupe by content hash)."""
    import random

    from PIL import Image

    img = Image.new("RGB", (320, 240), tuple(random.randrange(256) for _ in range(3)))
    img.putpixel((0, 0), (random.randrange(256), 7, 9))
    img.save(media_dir / "pc.jpg", "JPEG", quality=95)
    job = wait_job(client, client.post("/api/scan", json={"paths": [str(media_dir)]}).json()["job_id"])
    assert job["status"] == "done", job
    items = client.get("/api/assets", params={"kind": "photo", "limit": 500}).json()["items"]
    return next(a["id"] for a in items if a["path"].startswith(str(media_dir)))


# --- allow-list --------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "image.autoTone", "image.adjustments.curves", "filter.sharpen.smartSharpen",
    "layer.newAdjustmentLayer.vibrance", "image.imageRotation.90cw", "image.crop",
])
def test_allowed_commands(cmd):
    assert photocraft.is_allowed(cmd)


@pytest.mark.parametrize("cmd", [
    "file.save", "file.saveAs", "file.export.colorLookupTables", "automate.batch",
    "image.adjustments.colorLookup", "layer.newAdjustmentLayer.colorLookup",
    "image.adjustments.matchColor", "filter.other.displace", "app.quit",
    "image.adjustments", "image.adjustments.curves;rm", "",
])
def test_refused_commands(cmd):
    assert not photocraft.is_allowed(cmd)


@pytest.mark.parametrize("params", [
    {"path": "x.cube"},
    {"nested": {"File": "a.png"}},
    {"texture": "t.png"},
    {"name": "/etc/passwd"},
    {"name": "C:\\Windows\\x"},
    {"name": "../../secret"},
    {"list": [{"src": "a"}]},
])
def test_path_like_params_refused(params):
    with pytest.raises(photocraft.PhotoCraftError):
        photocraft.validate_steps([{"id": "image.adjustments.curves", "params": params}])


def test_step_limits():
    with pytest.raises(photocraft.PhotoCraftError):
        photocraft.validate_steps([])
    with pytest.raises(photocraft.PhotoCraftError):
        photocraft.validate_steps([{"id": "image.autoTone"}] * (photocraft.MAX_STEPS + 1))
    with pytest.raises(photocraft.PhotoCraftError):
        photocraft.validate_steps([{"id": "image.autoTone", "params": {"x": "a" * 20000}}])


def test_every_recipe_passes_its_own_validation():
    for name in photocraft.RECIPES:
        assert photocraft.resolve_steps(name, None)


def test_recipe_plus_steps_and_unknown_recipe():
    steps = photocraft.resolve_steps("auto", [{"id": "image.imageRotation.90cw", "params": {}}])
    assert [s[0] for s in steps] == ["image.autoTone", "image.autoColor", "image.imageRotation.90cw"]
    with pytest.raises(photocraft.PhotoCraftError):
        photocraft.resolve_steps("nope", None)


def test_output_path_is_deterministic_and_scoped():
    steps = photocraft.resolve_steps("punchy", None)
    a = photocraft.output_path(7, steps, "jpg", 92, "punchy")
    assert a == photocraft.output_path(7, steps, "jpg", 92, "punchy")
    assert a != photocraft.output_path(7, steps, "png", 92, "punchy")
    assert a.parent == photocraft.PHOTOCRAFT_DIR
    assert a.name.startswith("7_punchy_")


# --- API ---------------------------------------------------------------------
def test_status_without_cli(client, monkeypatch):
    monkeypatch.setenv("MF_PHOTOCRAFT_CLI", "/nonexistent/photocraft-cli")
    data = client.get("/api/photocraft/status").json()
    assert data["available"] is False
    assert "punchy" in data["recipes"]
    r = client.post("/api/photocraft/apply", json={"asset_id": 1, "recipe": "auto"})
    assert r.status_code == 503


def test_status_and_commands_filtered(client, fake_cli):
    data = client.get("/api/photocraft/status").json()
    assert data["available"] is True and "fake" in data["version"]
    ids = [c["id"] for c in client.get("/api/photocraft/commands").json()]
    assert ids == ["image.autoTone", "image.adjustments.vibrance"]


def test_recipes_endpoint(client):
    recipes = {r["name"]: r for r in client.get("/api/photocraft/recipes").json()}
    assert set(recipes) == set(photocraft.RECIPES)
    assert recipes["auto"]["steps"][0]["id"] == "image.autoTone"


def test_apply_runs_cli_and_writes_new_file(client, wait_job, media_dir, fake_cli):
    aid = _photo_id(client, wait_job, media_dir)
    src = client.get(f"/api/assets/{aid}").json()["path"]
    before = Path(src).read_bytes()

    r = client.post("/api/photocraft/apply", json={
        "asset_id": aid, "recipe": "punchy",
        "steps": [{"id": "image.imageRotation.90cw", "params": {}}],
        "format": "jpg", "quality": 88,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "photocraft"
    assert body["output_url"].startswith("/exports/photocraft/")
    job = wait_job(client, body["job_id"])
    assert job["status"] == "done", job
    assert Path(body["output_path"]).is_file()
    assert Path(src).read_bytes() == before, "original must never change"
    assert client.get(body["output_url"]).status_code == 200

    run = [c for c in _calls(fake_cli) if c[:1] == ["run"]][-1]
    assert run[1] == src
    assert run[run.index("--quality") + 1] == "88"
    cmds = [run[i + 1] for i, a in enumerate(run) if a == "--cmd"]
    assert cmds == ["image.autoContrast", "image.adjustments.vibrance",
                    "filter.sharpen.smartSharpen", "image.imageRotation.90cw"]
    out_arg = Path(run[run.index("--out") + 1])
    assert out_arg.parent.parent == photocraft.PHOTOCRAFT_DIR  # temp dir, then renamed

    # Same request again: cached, no second CLI run.
    n_runs = len([c for c in _calls(fake_cli) if c[:1] == ["run"]])
    again = client.post("/api/photocraft/apply", json={
        "asset_id": aid, "recipe": "punchy",
        "steps": [{"id": "image.imageRotation.90cw", "params": {}}],
        "format": "jpg", "quality": 88,
    }).json()
    assert wait_job(client, again["job_id"])["status"] == "done"
    assert again["output_path"] == body["output_path"]
    assert len([c for c in _calls(fake_cli) if c[:1] == ["run"]]) == n_runs


def test_apply_rejects_bad_requests(client, wait_job, media_dir, fake_cli):
    aid = _photo_id(client, wait_job, media_dir)
    bad = [
        ({"asset_id": aid, "steps": [{"id": "file.save", "params": {"path": "/tmp/x.psd"}}]}, 422),
        ({"asset_id": aid, "steps": [{"id": "image.adjustments.curves", "params": {"path": "a"}}]}, 422),
        ({"asset_id": aid, "recipe": "nope"}, 422),
        ({"asset_id": aid}, 422),
        ({"asset_id": aid, "recipe": "auto", "format": "exe"}, 422),
        ({"asset_id": 999999, "recipe": "auto"}, 404),
    ]
    for payload, code in bad:
        r = client.post("/api/photocraft/apply", json=payload)
        assert r.status_code == code, (payload, r.text)


def test_apply_surfaces_cli_failure(client, wait_job, media_dir, fake_cli):
    aid = _photo_id(client, wait_job, media_dir)
    r = client.post("/api/photocraft/apply", json={
        "asset_id": aid, "steps": [{"id": "image.adjustments.posterize", "params": {"mode": "boom"}}]})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "engine exploded" in job["message"]
    assert not Path(r.json()["output_path"]).exists()
    leftovers = [p for p in photocraft.PHOTOCRAFT_DIR.iterdir() if p.is_dir()]
    assert leftovers == [], "temp dirs must be cleaned up"


def _real_cli() -> str | None:
    cand = os.environ.get("MF_PHOTOCRAFT_CLI_REAL") or shutil.which("photocraft-cli")
    return cand if cand and os.path.isfile(cand) else None


@pytest.mark.skipif(_real_cli() is None, reason="real photocraft-cli not installed")
def test_real_cli_end_to_end(client, wait_job, media_dir, monkeypatch):
    monkeypatch.setenv("MF_PHOTOCRAFT_CLI", _real_cli())
    aid = _photo_id(client, wait_job, media_dir)
    names = {c["id"] for c in client.get("/api/photocraft/commands").json()}
    for recipe in photocraft.RECIPES.values():
        for step in recipe["steps"]:
            assert step["id"] in names, step["id"]
    for name in photocraft.RECIPES:
        r = client.post("/api/photocraft/apply", json={"asset_id": aid, "recipe": name, "format": "png"})
        assert r.status_code == 200, r.text
        job = wait_job(client, r.json()["job_id"], timeout=180)
        assert job["status"] == "done", (name, job)
        from PIL import Image
        with Image.open(r.json()["output_path"]) as im:
            assert im.size in ((320, 240), (240, 320))

"""Cloud (OpenRouter) planner: opt-in, key-gated, falls back cleanly."""

import httpx

from app import persist
from app.ai import cloud_llm
from app.edits import planner


def _plan_json(asset_id, scene_id, start, end):
    return (
        '{"summary": "Cloud reel", "target_ratio": "9:16", "total_duration": 4,'
        f' "clips": [{{"asset_id": {asset_id}, "scene_id": {scene_id}, "start": {start}, "end": {end},'
        ' "caption": "x", "transition": "crossfade", "score": 7}]}'
    )


def test_cloud_plan_skipped_when_toggle_off(monkeypatch):
    monkeypatch.setattr(persist, "use_cloud_llm", lambda: False)
    monkeypatch.setattr(cloud_llm, "generate_json", lambda p: (_ for _ in ()).throw(AssertionError("called")))
    assert planner._cloud_plan("reel", db=None) is None


def test_cloud_plan_skipped_without_key(monkeypatch):
    monkeypatch.setattr(persist, "use_cloud_llm", lambda: True)
    monkeypatch.setattr(cloud_llm, "api_key", lambda: "")
    assert planner._cloud_plan("reel", db=None) is None


def test_llm_plan_uses_generator_and_labels_source(monkeypatch):
    source = {"asset_id": 7, "scene_id": 3, "start": 0.0, "end": 10.0, "caption": "c", "score": 5.0}
    monkeypatch.setattr(planner, "_candidate_sources", lambda db, **kw: ([source], {}))
    monkeypatch.setattr(planner, "_normalize_clips",
                        lambda clips, db, allowed_ids=None: [c for c in clips if c["asset_id"] in allowed_ids])
    plan = planner._llm_plan("reel", None, lambda prompt: _plan_json(7, 3, 1.0, 5.0), "cloud")
    assert plan["source"] == "cloud"
    assert plan["summary"] == "Cloud reel"
    assert [c["asset_id"] for c in plan["clips"]] == [7]


def test_llm_plan_returns_none_on_provider_error(monkeypatch):
    source = {"asset_id": 7, "scene_id": 3, "start": 0.0, "end": 10.0, "caption": "c", "score": 5.0}
    monkeypatch.setattr(planner, "_candidate_sources", lambda db, **kw: ([source], {}))

    def boom(prompt):
        raise RuntimeError("OpenRouter returned HTTP 502")

    assert planner._llm_plan("reel", None, boom, "cloud") is None


def test_generate_json_sends_key_server_side_and_parses(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, auth=headers["Authorization"], body=json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    monkeypatch.setattr(cloud_llm, "api_key", lambda: "sk-test")
    monkeypatch.setattr(cloud_llm.httpx, "post", fake_post)
    assert cloud_llm.generate_json("prompt", model="deepseek/deepseek-v4.1-flash") == "{}"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_generate_json_error_does_not_leak_key(monkeypatch):
    monkeypatch.setattr(cloud_llm, "api_key", lambda: "sk-secret")
    monkeypatch.setattr(cloud_llm.httpx, "post",
                        lambda *a, **k: httpx.Response(401, json={"error": "bad key sk-secret"}))
    try:
        cloud_llm.generate_json("prompt")
    except RuntimeError as exc:
        assert "sk-secret" not in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

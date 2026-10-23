"""Offline contract tests for MediaForge MCP tools; no HTTP service is contacted."""
import sys
import types
import ast
import re
from pathlib import Path
import unittest

try:
    import mcp.server.fastmcp  # noqa: F401
except ModuleNotFoundError:
    # Keep these route-contract tests runnable in a lean backend test venv.
    # Production always imports the real FastMCP package from requirements.txt.
    class _FastMCP:
        def __init__(self, *_args, **_kwargs):
            pass
        def tool(self):
            return lambda func: func
    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _FastMCP
    sys.modules.update({"mcp": mcp_module, "mcp.server": server_module,
                        "mcp.server.fastmcp": fastmcp_module})

import server


class MediaForgeMcpOfflineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.original_call = server._call

    def tearDown(self):
        server._call = self.original_call

    def fake_call(self, replies):
        async def _fake(method, path, params=None, body=None):
            self.calls.append((method, path, params, body))
            return replies.pop(0)
        server._call = _fake

    async def test_captions_adds_download_urls(self):
        self.fake_call([
            {"cues": [], "qc": {"cue_count": 0, "issue_count": 0, "issues": []}, "style": "social_vertical"},
            {"words": [{"text": "editable", "start": 0, "end": .3}]},
        ])
        result = await server.mf_get_captions(17, "social_vertical")
        self.assertEqual(self.calls, [
            ("GET", "/api/assets/17/captions", {"style": "social_vertical"}, None),
            ("GET", "/api/assets/17/caption-words", None, None),
        ])
        self.assertEqual(result["words"], [{"text": "editable", "start": 0, "end": .3}])
        self.assertEqual(result["caption_words"]["words"], result["words"])
        self.assertEqual(
            result["downloads"]["srt"],
            f"{server.BASE}/api/assets/17/captions/srt?style=social_vertical",
        )
        self.assertTrue(result["downloads"]["vtt"].endswith("/captions/vtt?style=social_vertical"))
        self.assertTrue(result["downloads"]["ass"].endswith("/captions/ass?style=social_vertical"))

    async def test_caption_words_support_assets_and_plans(self):
        self.fake_call([{"words": [{"word": "hello"}]}])
        result = await server.mf_get_captions(None, plan_id="story plan")
        self.assertEqual(result["words"][0]["word"], "hello")
        self.assertEqual(self.calls, [("GET", "/api/edits/plan/story%20plan/caption-words", None, None)])

        self.calls.clear()
        self.fake_call([{"ok": True}])
        await server.mf_update_captions(asset_id=7, words=[{"word": "edited"}])
        self.assertEqual(self.calls, [("PUT", "/api/assets/7/caption-words", None,
                                       {"words": [{"word": "edited"}]})])

    async def test_new_edit_import_and_music_routes(self):
        self.fake_call([{"plan_id": "tight"}, {"versions": []}, {"ok": True},
                        {"plan_id": "swapped"}, {"pending": []}, {"job_id": "i1"}, []])
        await server.mf_tighten(asset_id=3, min_silence_s=.5)
        await server.mf_plan_versions("p 1")
        await server.mf_restore_plan("p 1", "2")
        await server.mf_swap_broll("p 1", 2, 11)
        await server.mf_list_pending_imports()
        await server.mf_approve_import("usb 1")
        await server.mf_list_music()
        self.assertEqual(self.calls[0], ("POST", "/api/edits/tighten", None, {
            "asset_id": 3, "plan_id": None, "silence_db": -35, "min_silence_s": .5,
            "remove_fillers": True, "padding_s": .08,
        }))
        self.assertEqual(self.calls[1], ("GET", "/api/edits/plan/p%201/versions", None, None))
        self.assertEqual(self.calls[2], ("POST", "/api/edits/plan/p%201/versions/2/restore", None, None))
        self.assertEqual(self.calls[3], ("POST", "/api/edits/plan/p%201/swap-broll", None,
                                         {"beat_index": 2, "asset_id": 11}))
        self.assertEqual(self.calls[4], ("GET", "/api/import/pending", None, None))
        self.assertEqual(self.calls[5], ("POST", "/api/import/pending/usb%201/approve", None, None))
        self.assertEqual(self.calls[6], ("GET", "/api/music", None, None))
        # Check bridge paths against actual backend route declarations, not
        # only mocked expectations that can repeat an integration mistake.
        app_dir = Path(__file__).resolve().parents[1] / "backend" / "app"
        routes = []
        for source in ("main.py", "caption_routes.py", "import_routes.py", "tighten_routes.py"):
            for node in ast.walk(ast.parse((app_dir / source).read_text())):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr in ("get", "post", "put") and decorator.args
                            and isinstance(decorator.args[0], ast.Constant)):
                        template = decorator.args[0].value
                        pattern = re.sub(r"\{[^}]+\}", "[^/]+", template)
                        routes.append((decorator.func.attr.upper(), pattern))
        for method, path, _params, _body in self.calls:
            self.assertTrue(any(verb == method and re.fullmatch(pattern, path)
                                for verb, pattern in routes), f"No backend route: {method} {path}")


    async def test_render_forwards_new_batch_and_reframe_options(self):
        self.fake_call([{"job_id": "render-1"}])
        await server.mf_render("plan-1", presets=["9:16", "1:1"],
                               reframe="smart", safe_zones={"top": .1},
                               guide_overlay=True, music="auto")
        body = self.calls[0][3]
        self.assertEqual((self.calls[0][0], self.calls[0][1]), ("POST", "/api/render"))
        self.assertEqual(body["presets"], ["9:16", "1:1"])
        self.assertEqual(body["safe_zones"], {"top": .1})
        self.assertTrue(body["guide_overlay"])
        self.assertEqual(body["music"], "auto")

    async def test_broll_and_story_content_batch_tools(self):
        self.fake_call([{"plan_id": "b1"}, {"job_id": "s1"}, {"status": "done", "meta": {"plan_id": "s"}},
                        {"plan_id": "s"}])
        await server.mf_create_broll_plan(music_path="/m/a.mp3", target_seconds=16, bars_per_cut=2,
                                          text_line="Build *more*", brand="albertaweb",
                                          broll_filter={"path_prefixes": ["/srv/x/"]}, seed=3)
        method, path, _p, body = self.calls[0]
        self.assertEqual((method, path), ("POST", "/api/edits/broll"))
        self.assertEqual(body["bars_per_cut"], 2)
        self.assertEqual(body["broll_filter"], {"path_prefixes": ["/srv/x/"]})
        self.assertEqual((body["silent"], body["ratio"], body["seed"]), (False, "9:16", 3))
        await server.mf_create_story_plan(
            "Hello world.", cloud_approved=True, voice_consent=True,
            broll_filter={"require_aerial": True, "exclude_faces": True}, beat_hints=["aerial city"],
            hook_text="Stop scrolling", brand="buildwithdb")
        body = self.calls[1][3]
        self.assertEqual(body["beat_hints"], ["aerial city"])
        self.assertEqual((body["hook_text"], body["brand"]), ("Stop scrolling", "buildwithdb"))
        self.assertTrue(body["broll_filter"]["exclude_faces"])
        self.calls.clear()
        self.fake_call([{"job_id": "r"}])
        await server.mf_render("p", music_start=12.5)
        self.assertEqual(self.calls[0][3]["music_start"], 12.5)

    async def test_footage_exclusion_tools(self):
        self.fake_call([{"asset_id": 7}, {"asset_id": 7, "removed": 1}, {"excluded": []}, {"plan_id": "p"}])
        await server.mf_exclude_footage(7, ranges=[[7, None]], reason="private house")
        await server.mf_exclude_footage(7, clear=True)
        await server.mf_list_excluded_footage()
        await server.mf_swap_broll("p", 2, 9, start=4.5)
        self.assertEqual(self.calls[0], ("POST", "/api/assets/7/exclude", None,
                                         {"ranges": [[7, None]], "all": False, "reason": "private house"}))
        self.assertEqual(self.calls[1], ("DELETE", "/api/assets/7/exclude", None, None))
        self.assertEqual(self.calls[2], ("GET", "/api/assets/excluded", None, None))
        self.assertEqual(self.calls[3][3], {"beat_index": 2, "asset_id": 9, "start": 4.5})
        self.calls.clear()
        self.fake_call([{"plan_id": "p"}])
        await server.mf_swap_broll("p", 1, 3)
        self.assertEqual(self.calls[0][3], {"beat_index": 1, "asset_id": 3})      # start omitted when unset

    async def test_story_beat_visuals_are_forwarded(self):
        self.fake_call([{"job_id": "s1"}, {"status": "done", "meta": {"plan_id": "p"}}, {"plan_id": "p"}])
        visuals = [{"type": "card", "card": {"kind": "stat", "text": "7 in 10", "sub": "websites run PHP"}}, None]
        await server.mf_create_story_plan("One. Two.", cloud_approved=True, voice_consent=True, beat_visuals=visuals)
        self.assertEqual(self.calls[0][3]["beat_visuals"], visuals)

    async def test_greenscreen_tool_posts_and_polls(self):
        self.fake_call([{"job_id": "g1"}, {"status": "running"}, {"status": "done", "meta": {"plan_id": "gp"}},
                        {"plan_id": "gp"}])
        orig = server.asyncio.sleep
        server.asyncio.sleep = lambda *_a, **_k: orig(0)
        try:
            result = await server.mf_create_greenscreen(
                source_path="/srv/x/selfie.mov", background_mode="mixed",
                backgrounds=[{"kind": "stat", "text": "7 in 10"}, "aerial city"], brand="albertaweb")
        finally:
            server.asyncio.sleep = orig
        self.assertEqual(result["plan_id"], "gp")
        method, path, _p, body = self.calls[0]
        self.assertEqual((method, path), ("POST", "/api/edits/greenscreen"))
        self.assertEqual((body["source_path"], body["background_mode"], body["brand"]),
                         ("/srv/x/selfie.mov", "mixed", "albertaweb"))
        self.assertEqual(body["backgrounds"][1], "aerial city")
        self.assertEqual(self.calls[-1][1], "/api/edits/plan/gp")

    async def test_render_legacy_default_does_not_enable_auto_music(self):
        self.fake_call([{"job_id": "render-legacy"}])
        await server.mf_render("plan-1")
        body = self.calls[0][3]
        self.assertIsNone(body["music"])
        self.assertIsNone(body["music_path"])

    async def test_call_sends_optional_token_only_as_bearer_header(self):
        captured = {}
        original_client, original_token = server.httpx.AsyncClient, server.API_TOKEN

        class Response:
            status_code = 200
            text = "{}"
            def json(self):
                return {"ok": True}

        class Client:
            def __init__(self, **_kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return False
            async def request(self, _method, _path, **kwargs):
                captured.update(kwargs)
                return Response()

        try:
            server.httpx.AsyncClient = Client
            server.API_TOKEN = "test-token"
            self.assertEqual(await server._call("GET", "/api/health"), {"ok": True})
        finally:
            server.httpx.AsyncClient, server.API_TOKEN = original_client, original_token
        self.assertEqual(captured["headers"], {"Authorization": "Bearer test-token"})

    async def test_handoff_package_requires_approved_plan(self):
        self.fake_call([{"plan_id": "good plan", "status": "approved"}])
        result = await server.mf_handoff_package("good plan")
        self.assertEqual(self.calls, [("GET", "/api/edits/plan/good%20plan", None, None)])
        self.assertEqual(result["download_url"], f"{server.BASE}/api/edits/plan/good%20plan/package")

        self.calls.clear()
        self.fake_call([{"plan_id": "draft", "status": "draft"}])
        with self.assertRaisesRegex(RuntimeError, "approved plan"):
            await server.mf_handoff_package("draft")
        self.assertEqual(len(self.calls), 1)

    async def test_story_forwards_per_job_approval_fields(self):
        self.fake_call([
            {"job_id": "story-1"},
            {"status": "done", "meta": {"plan_id": "plan-1"}},
            {"plan_id": "plan-1", "status": "draft"},
        ])
        result = await server.mf_create_story_plan(
            "A short narration.", voice_id="voice-1", cloud_approved=True,
            voice_consent=True, max_characters=42,
        )
        self.assertEqual(result["plan_id"], "plan-1")
        method, path, _params, body = self.calls[0]
        self.assertEqual((method, path), ("POST", "/api/edits/story"))
        self.assertEqual(body["cloud_approved"], True)
        self.assertEqual(body["voice_consent"], True)
        self.assertEqual(body["max_characters"], 42)

    async def test_story_rejects_missing_consent_or_cap_before_request(self):
        async def unexpected_call(*_args, **_kwargs):
            raise AssertionError("_call must not run")
        server._call = unexpected_call
        with self.assertRaisesRegex(RuntimeError, "cloud_approved"):
            await server.mf_create_story_plan("hello")
        with self.assertRaisesRegex(RuntimeError, "exceeding"):
            await server.mf_create_story_plan(
                "hello", cloud_approved=True, voice_consent=True, max_characters=4,
            )


if __name__ == "__main__":
    unittest.main()

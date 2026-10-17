"""Offline contract tests for MediaForge MCP tools; no HTTP service is contacted."""
import unittest

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
        self.fake_call([{"cues": [], "qc": {"cue_count": 0, "issue_count": 0, "issues": []}, "style": "social_vertical"}])
        result = await server.mf_get_captions(17, "social_vertical")
        self.assertEqual(self.calls, [("GET", "/api/assets/17/captions", {"style": "social_vertical"}, None)])
        self.assertEqual(
            result["downloads"]["srt"],
            f"{server.BASE}/api/assets/17/captions/srt?style=social_vertical",
        )
        self.assertTrue(result["downloads"]["vtt"].endswith("/captions/vtt?style=social_vertical"))
        self.assertTrue(result["downloads"]["ass"].endswith("/captions/ass?style=social_vertical"))

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

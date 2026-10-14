"""ComfyUI over its HTTP API (routes checked against ComfyUI's server.py, 2026-10).

    POST /prompt  {"prompt": <API-format workflow>, "client_id": ...} -> {"prompt_id", "number"}
    GET  /history/{prompt_id}  -> {prompt_id: {"outputs": {node_id: {"images"/"gifs"/"videos": [...]}}}}
    GET  /view?filename=&subfolder=&type=output   -> the file
    POST /free    {"unload_models": true, "free_memory": true}

Workflows are YOURS: export each from ComfyUI with "Save (API Format)" into
config/comfyui/<name>.json and put placeholders where AdLab should fill in values:
"{{prompt}}", "{{negative}}", "{{width}}", "{{height}}", "{{seed}}", "{{frames}}",
"{{filename_prefix}}". A string that is exactly one placeholder becomes that value's
type (so "{{width}}" -> 1080 as a number).

Not configured (COMFYUI_URL unset) -> `configured` is False and the Producer
falls back to brand text cards, saying so in the manifest.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import settings

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class ComfyError(RuntimeError):
    pass


def workflow_path(name: str) -> Path:
    return settings().config_dir / "comfyui" / f"{name}.json"


def load_workflow(name: str) -> Optional[dict]:
    p = workflow_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def fill(workflow: dict, values: dict[str, Any]) -> dict:
    """Replace {{placeholders}} everywhere in an API-format workflow."""
    missing: set[str] = set()

    def sub(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, str):
            whole = PLACEHOLDER.fullmatch(v)
            if whole:
                key = whole.group(1)
                if key not in values:
                    missing.add(key)
                    return v
                return values[key]
            def rep(m: re.Match) -> str:
                if m.group(1) not in values:
                    missing.add(m.group(1))
                    return m.group(0)
                return str(values[m.group(1)])
            return PLACEHOLDER.sub(rep, v)
        return v

    out = sub(copy.deepcopy(workflow))
    if missing:
        raise ComfyError(f"workflow placeholders without values: {sorted(missing)}")
    return out


class ComfyUI:
    def __init__(self, url: Optional[str] = None, http: Optional[httpx.Client] = None, timeout: float = 60):
        self.url = (url if url is not None else os.environ.get("COMFYUI_URL", "")).rstrip("/")
        self._http = http or httpx.Client(timeout=timeout)
        self.client_id = f"adlab-{uuid.uuid4().hex[:8]}"

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = self._http.request(method, f"{self.url}{path}", **kw)
        except httpx.HTTPError as exc:
            raise ComfyError(f"ComfyUI unreachable at {self.url}: {exc}") from None
        if r.status_code >= 400:
            raise ComfyError(f"ComfyUI {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r

    def free(self) -> None:
        """Unload ComfyUI's models so another GPU job (Whisper) has the VRAM."""
        self._req("POST", "/free", json={"unload_models": True, "free_memory": True})

    def payload(self, workflow: dict) -> dict:
        return {"prompt": workflow, "client_id": self.client_id}

    def submit(self, workflow: dict) -> str:
        data = self._req("POST", "/prompt", json=self.payload(workflow)).json()
        if data.get("error") or not data.get("prompt_id"):
            raise ComfyError(f"ComfyUI rejected the workflow: {data.get('error')} {data.get('node_errors')}")
        return data["prompt_id"]

    def wait(self, prompt_id: str, timeout_s: float = 900, poll_s: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            hist = self._req("GET", f"/history/{prompt_id}").json()
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(f"ComfyUI run {prompt_id} failed: {status.get('messages')}")
                return entry.get("outputs", {})
            time.sleep(poll_s)
        raise ComfyError(f"ComfyUI run {prompt_id} timed out after {timeout_s:.0f}s")

    def download(self, outputs: dict, dest: Path) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        files = []
        for node in outputs.values():
            for kind in ("images", "gifs", "videos"):
                for f in node.get(kind, []):
                    r = self._req("GET", "/view", params={"filename": f["filename"],
                                                          "subfolder": f.get("subfolder", ""),
                                                          "type": f.get("type", "output")})
                    path = dest / f["filename"]
                    path.write_bytes(r.content)
                    files.append(path)
        if not files:
            raise ComfyError("ComfyUI run produced no output files")
        return files

    def run(self, workflow: dict, dest: Path, timeout_s: float = 900) -> list[Path]:
        return self.download(self.wait(self.submit(workflow), timeout_s), dest)

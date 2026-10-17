"""Comfy SDK 0.3, v2 jobs/assets. v1 is used ONLY for Cloud node metadata."""

import copy
from pathlib import Path

import httpx

from .settings import key

BASE = "https://cloud.comfy.org"


def client():
    if not key("comfy"):
        raise ValueError("Configure a Comfy Cloud API key in Settings")
    import os

    from comfy_sdk import Comfy

    if os.environ.get("COMFY_BASE_URL", BASE).rstrip("/") != BASE:
        raise ValueError("This adapter requires Comfy Cloud; unset COMFY_BASE_URL")
    return Comfy(api_key=key("comfy"), timeout=60)


def node_info():
    if not key("comfy"):
        raise ValueError("Configure a Comfy Cloud API key in Settings")
    with httpx.Client(timeout=30) as c:
        r = c.get(BASE + "/api/object_info", headers={"X-API-Key": key("comfy")})
        r.raise_for_status()
        return r.json()


def validate(graph, mappings, metadata=None):
    if not isinstance(graph, dict) or not graph or "nodes" in graph:
        raise ValueError("Export Workflow (API), not the canvas/save JSON")
    if not isinstance(mappings, dict):
        raise ValueError("Workflow mappings must be an object")
    mapped = {
        (m.get("node"), m.get("input"))
        for m in mappings.values()
        if isinstance(m, dict)
    }
    for node_id, node in graph.items():
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("class_type"), str)
            or not isinstance(node.get("inputs"), dict)
        ):
            raise ValueError("Each workflow node needs class_type and inputs")
        if metadata is not None:
            spec = metadata.get(node["class_type"])
            if not spec:
                raise ValueError(
                    "Workflow contains a node unavailable in Comfy Cloud: "
                    + node["class_type"]
                )
            for name in spec.get("input", {}).get("required", {}):
                if name not in node["inputs"]:
                    raise ValueError("Workflow is missing required input: " + name)
            options = {
                **spec.get("input", {}).get("required", {}),
                **spec.get("input", {}).get("optional", {}),
            }
            for name, value in node["inputs"].items():
                definition = options.get(name, [])
                if (
                    (node_id, name) not in mapped
                    and definition
                    and isinstance(definition[0], list)
                    and not isinstance(value, list)
                    and value not in definition[0]
                ):
                    raise ValueError("Workflow input is unavailable in Cloud: " + name)
        for value in node["inputs"].values():
            if isinstance(value, list) and (
                len(value) != 2
                or str(value[0]) not in graph
                or not isinstance(value[1], int)
            ):
                raise ValueError("Invalid workflow node connection")
    for name, m in mappings.items():
        if (
            not isinstance(m, dict)
            or m.get("node") not in graph
            or m.get("input") not in graph[m["node"]]["inputs"]
            or m.get("type") not in ("text", "asset")
        ):
            raise ValueError("Mappings require node, input, and type (text or asset)")


def submit(request, paths, request_id):
    graph = copy.deepcopy(request["workflow"])
    mappings = request["mappings"]
    validate(graph, mappings, node_info())
    with client() as c:
        wf = c.workflows.from_json(graph)
        for name, m in mappings.items():
            value = request["inputs"][name]
            if m["type"] == "asset":
                value = c.assets.from_file(paths[int(value)])
            wf.set_input(m["node"], m["input"], value)
        job = c.submit(wf, idempotency_key=request_id, api_key=key("comfy"))
        return job.id


def poll(remote_id, directory):
    with client() as c:
        job = c.jobs.get(remote_id)
        outputs = []
        if job.status == "succeeded":
            for i, output in enumerate(job.outputs):
                suffix = Path(output.name).suffix.lower()
                if suffix not in (
                    ".png",
                    ".jpg",
                    ".webp",
                    ".mp4",
                    ".webm",
                    ".mov",
                    ".wav",
                    ".mp3",
                ):
                    continue
                target = directory / f"{i}{suffix}"
                if not target.exists():
                    partial = target.with_suffix(suffix + ".part")
                    try:
                        output.to_file(partial)
                        partial.replace(target)
                    finally:
                        partial.unlink(missing_ok=True)
                outputs.append(str(target))
        return {"status": job.status, "assets": outputs}


def cancel(remote_id):
    with client() as c:
        return c.jobs.get(remote_id).cancel().status

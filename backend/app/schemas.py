"""Pydantic request/response schemas matching the API contract exactly."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# --- System -----------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    gpu: str
    gpu_name: str = ""
    vram_mb: int = 0
    version: str
    models: dict[str, Any] = {}
    warnings: list[str] = []


class ConfigOut(BaseModel):
    use_cloud_llm: bool = False
    media_dirs: list[str] = []
    ollama_model: str = "qwen2.5vl:7b"
    resolve_available: bool = False


# --- Scan / ingest -----------------------------------------------------------
class ScanRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


class JobRefOut(BaseModel):
    job_id: str
    kind: str


class CancelRequest(BaseModel):
    job_id: str


class OkOut(BaseModel):
    ok: bool = True


class DedupePair(BaseModel):
    id: str
    asset_a: int
    asset_b: int
    path_a: str
    path_b: str
    distance: float = 0.0
    kind: str = "exact"


class DedupeOut(BaseModel):
    exact: list[DedupePair] = Field(default_factory=list)
    near: list[DedupePair] = Field(default_factory=list)


class DedupeResolveRequest(BaseModel):
    pair_id: str
    action: str  # keep_a | keep_b | delete_b


# --- Assets ------------------------------------------------------------------
class SceneOut(BaseModel):
    id: int
    start: float
    end: float
    caption: Optional[str] = None
    aesthetic: Optional[float] = None


class FaceOut(BaseModel):
    id: int
    cluster_id: Optional[int] = None
    bbox: Optional[list[float]] = None


class AssetOut(BaseModel):
    id: int
    path: str
    kind: str
    mime: Optional[str] = None
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    taken_at: Optional[str] = None
    added_at: Optional[str] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    aesthetic_score: Optional[float] = None
    caption: Optional[str] = None
    status: str = "pending"
    hash: str = ""
    tags: list[str] = Field(default_factory=list)
    faces: list[str] = Field(default_factory=list)
    scene_count: int = 0
    has_transcript: bool = False


class AssetDetailOut(AssetOut):
    scenes: list[SceneOut] = Field(default_factory=list)
    faces_detail: list[FaceOut] = Field(default_factory=list)


class AssetListOut(BaseModel):
    total: int
    items: list[AssetOut] = Field(default_factory=list)


class TranscriptOut(BaseModel):
    segments: list[dict[str, Any]] = Field(default_factory=list)


class ScenesOut(BaseModel):
    scenes: list[SceneOut] = Field(default_factory=list)


# --- Search ------------------------------------------------------------------
class SearchOut(BaseModel):
    query: str
    results: list[AssetOut] = Field(default_factory=list)
    took_ms: int = 0


# --- Faces -------------------------------------------------------------------
class FaceClusterOut(BaseModel):
    id: int
    name: Optional[str] = None
    count: int = 0
    thumb_asset_id: Optional[int] = None


class FacesOut(BaseModel):
    clusters: list[FaceClusterOut] = Field(default_factory=list)


class FaceNameRequest(BaseModel):
    name: str


class FaceMergeRequest(BaseModel):
    from_cluster: int
    into_cluster: int


# --- Edit planner ------------------------------------------------------------
class ClipOut(BaseModel):
    asset_id: int
    scene_id: Optional[int] = None
    start: float
    end: float
    caption: Optional[str] = None
    transition: str = "crossfade"
    score: float = 0.0


class PlanRequest(BaseModel):
    intent: str = "30-second highlight reel, upbeat"


class PlanOut(BaseModel):
    plan_id: str
    status: str
    summary: Optional[str] = None
    clips: list[ClipOut] = Field(default_factory=list)
    total_duration: float = 0.0
    target_ratio: str = "9:16"
    intent: Optional[str] = None
    created_at: Optional[str] = None


class PlanUpdateRequest(BaseModel):
    clips: list[ClipOut] = Field(default_factory=list)


class ApproveOut(BaseModel):
    ok: bool = True
    status: str = "approved"


# --- Render ------------------------------------------------------------------
class RenderRequest(BaseModel):
    plan_id: str
    ratio: str = "9:16"
    music_path: Optional[str] = None
    captions: bool = True
    width: int = 1080
    height: int = 1920


class RenderJobOut(BaseModel):
    job_id: str
    kind: str = "render"
    plan_id: str


class RenderStatusOut(BaseModel):
    status: str
    output_path: Optional[str] = None
    progress: float = 0.0


# --- Exports -----------------------------------------------------------------
class ExportRequest(BaseModel):
    plan_id: str


class ExportPathOut(BaseModel):
    ok: bool = True
    path: str


class ResolveExportOut(BaseModel):
    ok: bool = True
    resolve: str
    project: str


# --- Touchup -----------------------------------------------------------------
class TouchupRequest(BaseModel):
    asset_id: int
    preset: str = "auto_levels"


class TouchupOut(BaseModel):
    job_id: str
    kind: str = "touchup"
    preset: str
    output_path: Optional[str] = None


# --- Jobs --------------------------------------------------------------------
class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    progress: float = 0.0
    message: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None

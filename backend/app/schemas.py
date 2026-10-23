"""Pydantic request/response schemas matching the API contract exactly."""
from __future__ import annotations

from typing import Any, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- System -----------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    gpu: str
    gpu_name: str = ""
    vram_mb: int = 0
    version: str
    models: dict[str, Any] = {}
    warnings: list[str] = []
    # watcher per dir, scan queue, last catch-up scan, AI worker queue
    ingest: Optional[dict[str, Any]] = None


class ConfigOut(BaseModel):
    use_cloud_llm: bool = False
    media_dirs: list[str] = []
    ollama_model: str = "qwen2.5vl:7b"
    resolve_available: bool = False
    watcher_enabled: bool = True
    music_dir: str = ""


class ConfigUpdate(BaseModel):
    media_dirs: Optional[list[str]] = None
    watcher_enabled: Optional[bool] = None
    music_dir: Optional[str] = None
    use_cloud_llm: Optional[bool] = None


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
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    place_name: Optional[str] = None
    location_source: Optional[str] = None
    location_confidence: Optional[float] = None
    trip_id: Optional[int] = None
    gps_alt: Optional[float] = None
    motion: Optional[dict[str, Any]] = None
    excluded: Optional[list[dict[str, Any]]] = None     # ranges that edits must never use


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
    trip_id: Optional[int] = None
    auto_approve: bool = False


class PlanOut(BaseModel):
    plan_id: str
    status: str
    summary: Optional[str] = None
    clips: list[ClipOut] = Field(default_factory=list)
    total_duration: float = 0.0
    target_ratio: str = "9:16"
    intent: Optional[str] = None
    created_at: Optional[str] = None
    parsed_intent: Optional[dict[str, Any]] = None
    match_stats: Optional[dict[str, Any]] = None
    story: Optional[dict[str, Any]] = None
    broll: Optional[dict[str, Any]] = None


class BrollFilter(BaseModel):
    """Restrict which library videos story/B-roll plans may use."""
    model_config = ConfigDict(extra="forbid")
    path_prefixes: list[str] = Field(default_factory=list, max_length=20)
    require_aerial: bool = False
    exclude_faces: bool = False
    min_aesthetic: Optional[float] = Field(default=None, ge=0, le=10)
    asset_ids: list[int] = Field(default_factory=list, max_length=5000)
    exclude_asset_ids: list[int] = Field(default_factory=list, max_length=5000)
    # Camera-motion filters (need the motion analysis: POST /api/motion/analyze).
    min_smoothness: Optional[float] = Field(default=None, ge=0, le=1)
    hyperlapse: Optional[bool] = None            # true = only, false = exclude, null = any
    daypart: Optional[list[Literal["day", "dusk", "night"]]] = None


class StoryRequest(BaseModel):
    script: str = Field(min_length=1, max_length=50000)
    voice_id: Optional[str] = None
    trip_id: Optional[int] = None
    ratio: str = "16:9"
    title: Optional[str] = None
    caption_style: str = "kinetic"
    cloud_approved: bool = False
    voice_consent: bool = False
    max_characters: int = Field(default=5000, ge=1, le=50000)
    broll_filter: Optional[BrollFilter] = None
    # One search query per beat (replaces the beat text when matching footage).
    beat_hints: Optional[list[str]] = Field(default=None, max_length=200)
    hook_text: Optional[str] = Field(default=None, max_length=90)
    brand: Optional[str] = Field(default=None, max_length=40)
    # One entry per beat: null (drone footage) or {"type": "card", "card": {"kind": "headline|stat|alert|
    # terminal|timeline", "text": ..., "sub": ...}}. The card replaces that beat's footage for its voice slot.
    beat_visuals: Optional[list[Optional[dict[str, Any]]]] = Field(default=None, max_length=200)


class BrollPlanRequest(BaseModel):
    music_path: Optional[str] = Field(default=None, max_length=1024)
    music_start: Optional[float] = Field(default=None, ge=0, le=36000)
    target_seconds: float = Field(default=30.0, ge=30, le=90)
    bars_per_cut: Literal[1, 2] = 1
    text_line: str = Field(default="", max_length=90)
    hook_text: Optional[str] = Field(default=None, max_length=90)
    brand: Optional[str] = Field(default=None, max_length=40)
    broll_filter: Optional[BrollFilter] = None
    silent: bool = False
    asset_ids: Optional[list[int]] = Field(default=None, max_length=5000)
    ratio: Literal["9:16", "16:9", "4:5", "1:1"] = "9:16"
    title: Optional[str] = Field(default=None, max_length=120)
    seed: Optional[int] = None


class CompilationRequest(BaseModel):
    daypart: Literal["day", "dusk", "night"]
    include_dusk_in: Optional[Literal["day", "night"]] = None
    music_path: Optional[str] = Field(default=None, max_length=1024)
    music_start: Optional[float] = Field(default=None, ge=0, le=36000)
    brand: Optional[str] = Field(default=None, max_length=40)
    hook_text: Optional[str] = Field(default=None, max_length=90)
    text_line: str = Field(default="", max_length=90)
    ratio: Literal["9:16", "16:9", "4:5", "1:1"] = "9:16"
    order: Literal["time", "flow"] = "time"
    min_clip_s: float = Field(default=0.8, ge=0.2, le=10)
    min_seconds: Optional[float] = Field(default=None, ge=4, le=300)
    title: Optional[str] = Field(default=None, max_length=120)


class StoryJobOut(BaseModel):
    job_id: str
    kind: str = "story"


class PlanUpdateRequest(BaseModel):
    clips: list[ClipOut] = Field(default_factory=list)


class SwapBrollRequest(BaseModel):
    beat_index: int = Field(ge=1)
    asset_id: int = Field(ge=1)
    # Where in the replacement the beat's footage starts (default: 0, or the earliest
    # start that avoids excluded footage).
    start: Optional[float] = Field(default=None, ge=0)


class ExcludeRequest(BaseModel):
    """Exclude footage from every future edit: ``all`` or ranges [[start, end], ...] (end null = to the end)."""
    ranges: Optional[list[list[Optional[float]]]] = Field(default=None, max_length=50)
    all: bool = False
    reason: str = Field(default="", max_length=300)


class ApproveOut(BaseModel):
    ok: bool = True
    status: str = "approved"


# --- Render ------------------------------------------------------------------
class RenderRequest(BaseModel):
    plan_id: str
    # None = the story's own ratio for story plans, else 9:16 (1080x1920).
    ratio: Optional[str] = None
    music_path: Optional[str] = None
    captions: bool = True
    width: Optional[int] = None
    height: Optional[int] = None
    # Story plans only: "kinetic" (default) or "classic" lower-third SRT.
    caption_style: Optional[str] = None
    # Kinetic grade, slow push-in and glitch cuts at beat boundaries.
    effects: bool = True
    presets: Optional[list[Literal["9:16", "4:5", "1:1", "16:9"]]] = Field(default=None, min_length=1, max_length=4)
    reframe: Literal["smart", "center"] = "smart"
    safe_zones: Optional[dict[str, float]] = None
    guide_overlay: bool = False
    music: Optional[Literal["auto"]] = None
    music_auto: bool = False
    # Story/B-roll renders with a beat-mapped track: where in the track the video
    # starts (seconds). Default: the track's best section on a downbeat.
    music_start: Optional[float] = Field(default=None, ge=0, le=36000)


class RenderJobOut(BaseModel):
    job_id: str
    kind: str = "render"
    plan_id: str


class RenderStatusOut(BaseModel):
    status: str
    output_path: Optional[str] = None
    progress: float = 0.0
    output_paths: dict[str, str] = Field(default_factory=dict)


# --- Exports -----------------------------------------------------------------
class ExportRequest(BaseModel):
    plan_id: str


# --- Media import / export ---------------------------------------------------
class UploadOut(BaseModel):
    ok: bool = True
    path: str
    name: str
    size: int
    folder: str


class ImportFolderRequest(BaseModel):
    path: str


class ImportFolderOut(JobRefOut):
    src: str
    dest: str
    files: int
    bytes: int
    skipped: int = 0


class MediaCopyRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1)
    dest: Optional[str] = None
    include_sidecars: bool = True


class MediaCopyOut(JobRefOut):
    dest: str
    files: int
    bytes: int


class ExportPathOut(BaseModel):
    ok: bool = True
    path: str
    story_files: Optional[dict[str, str]] = None


class ResolveExportOut(BaseModel):
    ok: bool = True
    resolve: str
    project: str
    story: Optional[dict[str, Any]] = None


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
class JobCleanupRequest(BaseModel):
    older_than_minutes: float = 0.0
    delete_finished_older_than_days: Optional[float] = None


class JobCleanupOut(BaseModel):
    interrupted: int = 0
    deleted: int = 0


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    progress: float = 0.0
    message: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class PlaceOut(BaseModel):
    city: str
    region: Optional[str] = None
    count: int = 0
    lat: Optional[float] = None
    lon: Optional[float] = None


class TripOut(BaseModel):
    id: int
    title: str
    city: Optional[str] = None
    region: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    asset_count: int = 0
    cover_asset_id: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_km: Optional[float] = None


class TripDetailOut(TripOut):
    assets: list[AssetOut] = Field(default_factory=list)


class TripUpdateRequest(BaseModel):
    title: str


class TripReelRequest(BaseModel):
    intent: Optional[str] = None


class FsStatRequest(BaseModel):
    path: str


class FsStatOut(BaseModel):
    path: str = ""
    exists: bool = False
    is_dir: bool = False
    videos: int = 0
    photos: int = 0
    sample_count: int = 0


class MusicTrackOut(BaseModel):
    name: str
    path: str
    ext: str = ""
    duration: Optional[float] = None
    loudness: Optional[float] = None
    rough_bpm: Optional[float] = None
    mood_tags: list[str] = Field(default_factory=list)

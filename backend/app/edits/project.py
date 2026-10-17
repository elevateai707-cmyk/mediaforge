"""Versioned, non-destructive project documents and immutable revisions."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from .. import models
from ..db import SessionLocal
from .style_pack import FONTS, PRESETS


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Word(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=10000)

    @model_validator(mode="after")
    def timing(self):
        if self.end <= self.start:
            raise ValueError("End must be after start")
        return self


class Cue(Word):
    words: list[Word] = Field(default_factory=list, max_length=500)


Hex = r"^#[0-9A-Fa-f]{6}$"
FontName = Literal[FONTS]  # type: ignore[valid-type]
PresetName = Literal[("clean", "bold", "active", *PRESETS)]  # type: ignore[valid-type]
Entrance = Literal["none", "fade", "blur", "rise", "blur-rise", "pop", "split"]


class Style(Strict):
    # "clean"/"bold"/"active" are the original presets. Any other value is a named
    # style-pack preset (see style_pack.PRESETS): its fields fill in whatever the
    # plan does not set explicitly, so `{"preset": "serif-card"}` alone is enough.
    preset: PresetName = "clean"
    font: FontName = "DejaVu Sans"
    size: int = Field(default=52, ge=12, le=320)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: int = Field(default=2, ge=0, le=12)
    background: bool = False
    position: Literal["bottom", "center", "top"] = "bottom"
    max_lines: int = Field(default=2, ge=1, le=5)
    margin: float = Field(default=0.08, ge=0.02, le=0.3)
    # Designed-caption options (all off by default so existing projects render
    # unchanged). Any of `highlight`, `glow` or `y` switches the cue to the
    # per-line "designed" layout in subtitles.py.
    bold: bool = False
    # Colour for words wrapped in *asterisks*; None leaves asterisks as text.
    highlight: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    # Glow radius in px at 1920 tall: a soft dark shadow behind all text plus a
    # highlight-coloured glow behind emphasised words. 0 = off.
    glow: int = Field(default=0, ge=0, le=60)
    # Vertical centre of the text block as a fraction of frame height (0 = top).
    y: float | None = Field(default=None, ge=0.05, le=0.95)
    # Longest line may fill at most this fraction of the frame width; the font
    # shrinks (never grows past `size`) to fit.
    max_width: float = Field(default=0.8, ge=0.3, le=1)
    # Line pitch as a multiple of the font size (designed layout only).
    line_spacing: float = Field(default=0.95, ge=0.6, le=2)
    # Fade-in in milliseconds (designed layout only).
    fade_in: int = Field(default=0, ge=0, le=2000)
    # Opacity of the dark shadow layer behind the text when `glow` is on.
    shade: float = Field(default=0.625, ge=0, le=1)
    # ---- style-pack options (all default off; any of them, or a pack preset,
    # switches the cue to the pack layout in subtitles.pack_events) ----
    # Letter spacing in px at 1920 tall, and forced case.
    spacing: float = Field(default=0, ge=-10, le=60)
    case: Literal["none", "upper", "lower"] = "none"
    # Horizontal centre as a fraction of frame width (None = centred).
    x: float | None = Field(default=None, ge=0.05, le=0.95)
    # Secondary colour (two-tone headlines, RGB-split copy, checklist tick).
    accent: str | None = Field(default=None, pattern=Hex)
    # 2-5 colours blended left to right across each line, per character.
    gradient: list[str] = Field(default_factory=list, max_length=5)
    # Outline-only lettering: transparent fill, `color` stroke of width `outline`.
    hollow: bool = False
    outline_color: str = Field(default="#000000", pattern=Hex)
    entrance: Entrance = "none"
    entrance_ms: int = Field(default=260, ge=40, le=2000)
    exit_ms: int = Field(default=0, ge=0, le=2000)
    # "word": show the cue's words (cue.words from whisper/manual timings, or an
    # even split of cue.text) `words_per` at a time, hard-cut on the beat.
    word_mode: Literal["phrase", "word"] = "phrase"
    words_per: int = Field(default=1, ge=1, le=6)
    # Key words (listed here, or written *like this*) switch to key_font/key_scale/
    # key_color. Matching ignores case and punctuation.
    keywords: list[str] = Field(default_factory=list, max_length=100)
    key_font: FontName | None = None
    key_scale: float = Field(default=1.0, ge=0.5, le=3)
    key_color: str | None = Field(default=None, pattern=Hex)

    @model_validator(mode="before")
    @classmethod
    def apply_preset(cls, data):
        if isinstance(data, dict) and data.get("preset") in PRESETS:
            return {**PRESETS[data["preset"]], **data}
        return data

    @model_validator(mode="after")
    def check_gradient(self):
        import re as _re

        if self.gradient and (
            len(self.gradient) < 2
            or any(not _re.match(Hex, c) for c in self.gradient)
        ):
            raise ValueError("Gradient needs 2-5 #RRGGBB colours")
        return self

    @property
    def designed(self):
        return bool(self.highlight or self.glow or self.y is not None)

    @property
    def packed(self):
        return bool(
            self.preset in PRESETS
            or self.spacing
            or self.case != "none"
            or self.x is not None
            or self.accent
            or self.gradient
            or self.hollow
            or self.entrance != "none"
            or self.exit_ms
            or self.word_mode != "phrase"
            or self.keywords
            or self.key_font
        )


class Inset(Strict):
    """Picture/clip tile composited under the text. Geometry is a fraction of the
    frame, top-left origin; `radius` is a fraction of the tile's shorter side."""

    x: float = Field(default=0.05, ge=-0.5, le=1)
    y: float = Field(default=0.05, ge=-0.5, le=1)
    w: float = Field(default=0.4, gt=0.02, le=1.5)
    h: float = Field(default=0.25, gt=0.02, le=1.5)
    radius: float = Field(default=0.08, ge=0, le=0.5)
    src_start: float = Field(default=0, ge=0)  # seek point for video media
    gray: bool = False
    shadow: bool = True
    border: str | None = Field(default=None, pattern=Hex)
    anim: Literal["none", "fade", "slide-up", "slide-down", "pop"] = "fade"
    anim_ms: int = Field(default=250, ge=0, le=2000)


class Overlay(Cue):
    style: Style = Field(default_factory=Style)
    # "text" is the normal on-video text. The others are timeline_overlays-only:
    #   solid     full-frame colour card behind later text (`fill`)
    #   checklist rounded card with `items`; row k ticks at start + check_at[k]
    #   inset     picture/clip tile (`media` + `inset` geometry)
    kind: Literal["text", "solid", "checklist", "inset"] = "text"
    fill: str | None = Field(default=None, pattern=Hex)
    items: list[str] = Field(default_factory=list, max_length=12)
    check_at: list[float] = Field(default_factory=list, max_length=12)
    media: str | None = Field(default=None, max_length=4096)
    inset: Inset | None = None

    @model_validator(mode="after")
    def kind_fields(self):
        if self.kind == "solid" and not self.fill:
            raise ValueError("A solid card needs a fill colour")
        if self.kind == "checklist" and not self.items:
            raise ValueError("A checklist needs items")
        if self.kind == "inset" and not (self.media and self.inset):
            raise ValueError("An inset needs media and inset geometry")
        if any(t < 0 for t in self.check_at):
            raise ValueError("Check times cannot be negative")
        return self


class Clip(Strict):
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex)
    asset_id: int = Field(gt=0)
    start: float = Field(default=0, ge=0)
    end: float = Field(gt=0)
    speed: float = Field(default=1, ge=0.25, le=4)
    transition: Literal["cut", "crossfade"] = "crossfade"
    # Replace this clip's own audio with silence (keeps the timeline length).
    mute: bool = False
    # Colour grade: "bw" desaturates the clip; with `reveal_at` (seconds into the
    # clip, after speed) colour fades back over `reveal_dur` (process -> result).
    look: Literal["color", "bw"] = "color"
    reveal_at: float | None = Field(default=None, ge=0, le=3600)
    reveal_dur: float = Field(default=0.4, ge=0.04, le=10)

    @model_validator(mode="after")
    def timing(self):
        if self.end <= self.start:
            raise ValueError("Clip end must exceed start")
        return self


class GainPoint(Strict):
    t: float = Field(ge=0, le=36000)  # final-timeline seconds
    db: float = Field(ge=-80, le=24)


class Audio(Strict):
    enabled: bool = True
    path: str | None = None
    volume: float = Field(default=1, ge=0, le=3)
    start: float = Field(default=0, ge=0)
    fade_in: float = Field(default=0, ge=0, le=30)
    fade_out: float = Field(default=0, ge=0, le=30)
    # Optional gain automation (dB), linear in dB between points, held outside
    # the first/last point. Applied on top of `volume`; empty = off.
    gain_points: list[GainPoint] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def ordered_gain(self):
        ts = [g.t for g in self.gain_points]
        if ts != sorted(ts) or len(set(ts)) != len(ts):
            raise ValueError("Gain points must have strictly increasing times")
        return self


class Export(Strict):
    fit: Literal["contain", "cover"] = "contain"
    ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    width: int = Field(default=1080, ge=160, le=3840, multiple_of=2)
    fps: int = Field(default=0, ge=0, le=120)
    codec: Literal["libx264", "h264_nvenc"] = "libx264"
    clean_master: bool = False
    sidecars: list[Literal["srt", "vtt", "ass"]] = Field(default_factory=list)
    hdr: Literal["tonemap", "reject"] = "tonemap"
    # Final output limiter ceiling (linear). 0.95 matches earlier renders.
    limit: float = Field(default=0.95, ge=0.1, le=1)
    # alimiter's auto-level re-normalises toward the ceiling (louder than the
    # mix); turn it off for a true peak ceiling.
    auto_level: bool = True

    @model_validator(mode="after")
    def frame_rate(self):
        if 0 < self.fps < 15:
            raise ValueError("Use 0 to match the first source, or 15–120 fps")
        return self


class Project(Strict):
    schema_version: Literal[2] = 2
    revision: int = Field(default=1, ge=1)
    clips: list[Clip] = Field(default_factory=list, max_length=500)
    speech_captions: bool = False
    on_video_text: bool = False
    # source subtitles are keyed by clip uid; narration cues use final timeline.
    captions: dict[str, list[Cue]] = Field(default_factory=dict)
    narration_captions: list[Cue] = Field(default_factory=list)
    caption_source: Literal["original", "narration"] = "original"
    overlays: dict[str, list[Overlay]] = Field(default_factory=dict)
    # On-video text placed directly on the final timeline (survives re-cuts).
    timeline_overlays: list[Overlay] = Field(default_factory=list, max_length=500)
    caption_style: Style = Field(default_factory=Style)
    original_audio: Audio = Field(default_factory=Audio)
    voice_over: Audio = Field(default_factory=lambda: Audio(enabled=False))
    music: Audio = Field(default_factory=lambda: Audio(enabled=False, volume=0.2))
    ducking: bool = True
    ducking_ratio: float = Field(default=8, ge=1, le=20)
    post_title: str = Field(default="", max_length=1000)
    post_description: str = Field(default="", max_length=20000)
    hashtags: str = Field(default="", max_length=2000)
    narration_script: str = Field(default="", max_length=20000)
    generated_assets: list[str] = Field(default_factory=list)
    export: Export = Field(default_factory=Export)

    @model_validator(mode="after")
    def unique_clips(self):
        if len({c.uid for c in self.clips}) != len(self.clips):
            raise ValueError("Each clip needs a unique ID")
        for items in self.overlays.values():
            if any(o.kind != "text" for o in items):
                raise ValueError("Cards, checklists and insets belong in timeline_overlays")
        return self


def timeline(project: Project):
    offset = 0.0
    result = []
    for i, c in enumerate(project.clips):
        duration = (c.end - c.start) / c.speed
        overlap = 0.0
        if i and project.clips[i - 1].transition == "crossfade":
            overlap = min(0.3, result[-1]["duration"] / 2, duration / 2)
        offset -= overlap
        result.append(dict(clip=c, start=offset, duration=duration, overlap=overlap))
        offset += duration
    return result


def migrate_plan(plan, db):
    p = Project(
        export=Export(ratio=plan.target_ratio or "9:16"), post_title=plan.summary or ""
    )
    for c in plan.clips:
        clip = Clip(asset_id=c.asset_id, start=c.start, end=c.end)
        p.clips.append(clip)
        if c.caption:
            p.overlays[clip.uid] = [Overlay(start=c.start, end=c.end, text=c.caption)]
        rows = (
            db.query(models.TranscriptSegment)
            .filter_by(asset_id=c.asset_id)
            .order_by(models.TranscriptSegment.start)
            .all()
        )
        p.captions[clip.uid] = [
            Cue(start=r.start, end=r.end, text=r.text or "")
            for r in rows
            if r.end > r.start
        ]
        stored = db.get(models.SystemSetting, f"words:{c.asset_id}")
        if stored:
            from .subtitles import phrase_cues

            words = [
                Word(
                    start=w["start"],
                    end=w["end"],
                    text=w.get("word", w.get("text", "")),
                )
                for w in json.loads(stored.value)
                if w["end"] > w["start"]
            ]
            if words:
                p.captions[clip.uid] = phrase_cues(words)
    return p


def get_project(plan_id):
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        plan = db.get(models.EditPlan, plan_id)
        if not plan:
            raise ValueError("Plan not found")
        row = db.get(models.ProjectDocument, plan_id)
        if not row:
            p = migrate_plan(plan, db)
            row = models.ProjectDocument(plan_id=plan_id, document=p.model_dump_json())
            db.add(row)
            db.add(
                models.ProjectRevision(
                    plan_id=plan_id, revision=1, document=row.document
                )
            )
            db.commit()
        return {
            "project": json.loads(row.document),
            "approved_revision": row.approved_revision,
        }


def save_project(plan_id, project):
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(models.ProjectDocument, plan_id)
        if not row:
            raise ValueError("Load the project before saving")
        old = Project.model_validate_json(row.document)
        if project.revision != old.revision:
            raise ValueError("Project changed in another tab. Reload before saving.")
        for c in project.clips:
            a = db.get(models.Asset, c.asset_id)
            if not a:
                raise ValueError("Unknown asset")
            if a.duration and c.end > float(a.duration) + 0.1:
                raise ValueError("Clip exceeds source duration")
        from ..security import media_path

        for a in (project.music, project.voice_over):
            if a.path:
                media_path(a.path, db)
        for o in project.timeline_overlays:
            if o.kind == "inset":
                media_path(o.media, db, images=True)
        project.revision += 1
        row.document = project.model_dump_json()
        row.approved_revision = None
        row.approved_document = None
        db.add(
            models.ProjectRevision(
                plan_id=plan_id, revision=project.revision, document=row.document
            )
        )
        db.get(models.EditPlan, plan_id).status = "draft"
        db.commit()
    return get_project(plan_id)


def approve(plan_id, revision):
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(models.ProjectDocument, plan_id)
        if not row:
            raise ValueError("Project not found")
        p = Project.model_validate_json(row.document)
        if p.revision != revision:
            raise ValueError("Save and review the current revision first")
        if not p.clips:
            raise ValueError("Select footage first")
        row.approved_revision = revision
        row.approved_document = row.document
        plan = db.get(models.EditPlan, plan_id)
        plan.status = "approved"
        # Retain existing NLE exporters: source clips synchronized, advanced features documented.
        for c in list(plan.clips):
            db.delete(c)
        db.flush()
        for i, c in enumerate(p.clips):
            db.add(
                models.EditClip(
                    plan_id=plan_id,
                    position=i,
                    asset_id=c.asset_id,
                    start=c.start,
                    end=c.end,
                    transition=c.transition,
                )
            )
        db.commit()
    return get_project(plan_id)


def snapshot(plan_id, approved=True):
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(models.ProjectDocument, plan_id)
        if not row or (approved and not row.approved_document):
            raise ValueError("Approve the current project first")
        p = Project.model_validate_json(
            row.approved_document if approved else row.document
        )
        paths = {}
        for c in p.clips:
            a = db.get(models.Asset, c.asset_id)
            if not a:
                raise ValueError("Missing source asset")
            paths[c.asset_id] = {"path": a.path, "kind": a.kind}
        return p.model_dump(), paths

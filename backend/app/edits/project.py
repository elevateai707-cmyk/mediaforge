"""Versioned, non-destructive project documents and immutable revisions."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from .. import models
from ..db import SessionLocal


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


class Style(Strict):
    preset: Literal["clean", "bold", "active"] = "clean"
    font: Literal["DejaVu Sans", "Liberation Sans"] = "DejaVu Sans"
    size: int = Field(default=52, ge=12, le=160)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: int = Field(default=2, ge=0, le=12)
    background: bool = False
    position: Literal["bottom", "center", "top"] = "bottom"
    max_lines: int = Field(default=2, ge=1, le=5)
    margin: float = Field(default=0.08, ge=0.02, le=0.3)


class Overlay(Cue):
    style: Style = Field(default_factory=Style)


class Clip(Strict):
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex)
    asset_id: int = Field(gt=0)
    start: float = Field(default=0, ge=0)
    end: float = Field(gt=0)
    speed: float = Field(default=1, ge=0.25, le=4)
    transition: Literal["cut", "crossfade"] = "crossfade"

    @model_validator(mode="after")
    def timing(self):
        if self.end <= self.start:
            raise ValueError("Clip end must exceed start")
        return self


class Audio(Strict):
    enabled: bool = True
    path: str | None = None
    volume: float = Field(default=1, ge=0, le=3)
    start: float = Field(default=0, ge=0)
    fade_in: float = Field(default=0, ge=0, le=30)
    fade_out: float = Field(default=0, ge=0, le=30)


class Export(Strict):
    ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    width: int = Field(default=1080, ge=160, le=3840, multiple_of=2)
    fps: int = Field(default=0, ge=0, le=120)
    codec: Literal["libx264", "h264_nvenc"] = "libx264"
    clean_master: bool = False
    sidecars: list[Literal["srt", "vtt", "ass"]] = Field(default_factory=list)
    hdr: Literal["tonemap", "reject"] = "tonemap"

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

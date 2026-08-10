"""SQLAlchemy ORM models for MediaForge."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, ForeignKey, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True)
    path = Column(String, unique=True, nullable=False, index=True)
    hash = Column(String, index=True, nullable=False)
    kind = Column(String, nullable=False)          # photo | video
    mime = Column(String, nullable=True)
    size = Column(Integer, default=0)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration = Column(Float, nullable=True)
    taken_at = Column(DateTime, nullable=True)
    added_at = Column(DateTime, default=_utcnow)
    camera_make = Column(String, nullable=True)
    camera_model = Column(String, nullable=True)
    gps_lat = Column(Float, nullable=True)
    gps_lon = Column(Float, nullable=True)
    aesthetic_score = Column(Float, nullable=True)
    caption = Column(Text, nullable=True)
    # Resumable pipeline status:
    # pending -> embedding_done -> scenes_done -> transcript_done ->
    # faces_done -> captioned -> indexed
    status = Column(String, default="pending", index=True)
    status_error = Column(Text, nullable=True)
    phash = Column(String, nullable=True)
    thumb_path = Column(String, nullable=True)
    poster_path = Column(String, nullable=True)
    proxy_path = Column(String, nullable=True)
    scene_count = Column(Integer, default=0)
    has_transcript = Column(Integer, default=0)

    scenes = relationship("Scene", back_populates="asset",
                          cascade="all, delete-orphan")
    transcript = relationship("TranscriptSegment", back_populates="asset",
                              cascade="all, delete-orphan")
    faces = relationship("FaceDetection", back_populates="asset",
                         cascade="all, delete-orphan")


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("asset_id", "index", name="uq_scene_asset_index"),)

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    index = Column(Integer, default=0)
    start = Column(Float, default=0.0)
    end = Column(Float, default=0.0)
    caption = Column(Text, nullable=True)
    aesthetic_score = Column(Float, nullable=True)
    frame_path = Column(String, nullable=True)

    asset = relationship("Asset", back_populates="scenes")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    start = Column(Float, default=0.0)
    end = Column(Float, default=0.0)
    text = Column(Text, nullable=True)

    asset = relationship("Asset", back_populates="transcript")


class FaceDetection(Base):
    __tablename__ = "face_detections"

    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    cluster_id = Column(Integer, nullable=True, index=True)
    embedding = Column(Text, nullable=True)   # JSON float32 list
    bbox = Column(Text, nullable=True)        # JSON [x1,y1,x2,y2]
    frame_time = Column(Float, nullable=True)  # seconds into video; None for photos

    asset = relationship("Asset", back_populates="faces")


class EditPlan(Base):
    __tablename__ = "edit_plans"

    id = Column(String, primary_key=True)
    intent = Column(Text, nullable=True)
    status = Column(String, default="draft", index=True)  # draft | approved
    summary = Column(Text, nullable=True)
    target_ratio = Column(String, default="9:16")
    total_duration = Column(Float, default=0.0)
    created_at = Column(DateTime, default=_utcnow)

    clips = relationship("EditClip", back_populates="plan",
                         cascade="all, delete-orphan", order_by="EditClip.position")


class EditClip(Base):
    __tablename__ = "edit_clips"

    id = Column(Integer, primary_key=True)
    plan_id = Column(String, ForeignKey("edit_plans.id"), nullable=False, index=True)
    position = Column(Integer, default=0)
    asset_id = Column(Integer, nullable=False)
    scene_id = Column(Integer, nullable=True)
    start = Column(Float, default=0.0)
    end = Column(Float, default=0.0)
    caption = Column(Text, nullable=True)
    transition = Column(String, default="crossfade")
    score = Column(Float, default=0.0)

    plan = relationship("EditPlan", back_populates="clips")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False, index=True)  # scan|ai|render|touchup
    status = Column(String, default="running")          # running|done|error|cancelled
    progress = Column(Float, default=0.0)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    meta = Column(Text, nullable=True)  # JSON


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)

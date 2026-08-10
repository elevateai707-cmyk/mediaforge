"""AI pipeline: CLIP embeddings, whisper transcripts, scene detection,
face clustering, aesthetic scoring and image captioning."""

from app.ai import aesthetic, captions, clip, faces, scenes, whisper
from app.ai import pipeline  # noqa: F401  (wired by main.py)

__all__ = ["aesthetic", "captions", "clip", "faces", "pipeline", "scenes", "whisper"]

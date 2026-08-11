"""Environment hygiene for external tool subprocesses (ffmpeg/ffprobe/exiftool).

The DaVinci Resolve bridge needs ``/opt/resolve/libs`` on ``LD_LIBRARY_PATH``,
but Resolve ships its own copies of the FFmpeg shared libraries. Any child
process that inherits that loader path picks up Resolve's ``libavutil`` under
the system ``ffmpeg`` and dies before it runs a single frame::

    ffmpeg: symbol lookup error: /lib/x86_64-linux-gnu/libswresample.so.4:
            undefined symbol: av_bessel_i0, version LIBAVUTIL_58   (exit 127)

Because ``os.environ`` is process-global, a single Resolve export — including
one that *fails* — used to poison every later thumbnail, proxy, scene frame and
render until the backend was restarted. ``tool_env()`` strips the loader
variables so external media tools always run against the system libraries,
whatever the server process was started with.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

# Variables that redirect the dynamic loader for the child process.
LOADER_VARS = ("LD_LIBRARY_PATH", "LD_PRELOAD")


def tool_env(base: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """A copy of the environment with dynamic-loader overrides removed."""
    env = dict(os.environ if base is None else base)
    for var in LOADER_VARS:
        env.pop(var, None)
    return env

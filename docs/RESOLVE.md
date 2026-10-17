# DaVinci Resolve 21 — Linux scripting setup (MediaForge bridge)

MediaForge's `POST /api/export/resolve` endpoint drives a *live* DaVinci
Resolve project over its Python scripting API: it creates a project, imports
media into bins, builds the timeline from an approved edit plan, and adds
markers, colored clip flags, and a subtitle track. This document covers the
one-time setup on Linux and the test snippet the backend runs.

## How the backend connects

Resolve ships its scripting module with the application. On Linux:

| Item | Path |
|---|---|
| Resolve install | `/opt/resolve` |
| Python module | `/opt/resolve/Developer/Scripting/Modules/DaVinciResolveScript.py` |
| Native bridge lib | `/opt/resolve/libs/Fusion/fusionscript.so` |

The backend automatically retries the connection with these environment
variables set for its subprocess:

```bash
export LD_LIBRARY_PATH=/opt/resolve/libs:$LD_LIBRARY_PATH
export PYTHONPATH=/opt/resolve/Developer/Scripting/Modules:$PYTHONPATH
```

You normally never need to touch these — the backend sets them itself. You
only need them for the manual test below.

## Prerequisites

1. **DaVinci Resolve 21 must be RUNNING.** The scripting API is a
   client–server connection to the running application — there is no
   headless mode. Start Resolve, sign in (free version is fine), and leave
   it open while you trigger `/api/export/resolve`.
2. **Enable external scripting.** In Resolve:
   `Preferences → System → General →` set the **"External scripting using"**
   dropdown to **Network/Local** (Resolve 21 wording; older versions call
   the same setting "External scripting using" with options *None / Local /
   Network / Network and Local*). Choose **Network/Local**.
3. Apply/OK the preference. No restart of Resolve is required for this
   setting, but the setting only takes effect for newly launched script
   connections.

## Manual test snippet

Run this from any Python 3 with the env vars above (or just run it with the
backend's venv — the backend prepends the paths automatically):

```bash
cd /home/bfam/mediaforge/backend
export LD_LIBRARY_PATH=/opt/resolve/libs:$LD_LIBRARY_PATH
export PYTHONPATH=/opt/resolve/Developer/Scripting/Modules:$PYTHONPATH
.venv/bin/python - <<'PY'
import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")
print("resolve object:", resolve)

if resolve is not None:
    print("version:", resolve.GetVersionString())
    pm = resolve.GetProjectManager()
    print("project:", pm.GetCurrentProject().GetName())
else:
    print("NONE — Resolve is not running or external scripting is disabled.")
PY
```

- `resolve object: <DaVinciResolve object>` — success, the API is live.
- `resolve object: None` — the script *loaded* but could not connect. See
  troubleshooting below.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'DaVinciResolveScript'` | `PYTHONPATH` missing `/opt/resolve/Developer/Scripting/Modules`, or Resolve is not installed at `/opt/resolve` (that is the fixed install path on Linux). Verify the file exists: `ls /opt/resolve/Developer/Scripting/Modules/`. |
| `OSError: fusionscript.so: cannot open shared object file` | `LD_LIBRARY_PATH=/opt/resolve/libs` not set when importing. |
| `resolve.scriptapp("Resolve")` returns `None` | Resolve is **not running**, or "External scripting using" is not set to **Network/Local**. Start Resolve, check Preferences → System → General, then retry. No restart needed after changing the setting. |
| Import works in a terminal but `/api/export/resolve` still 503s | The backend's 503 message echoes exactly this doc's fix. Also make sure the backend process and Resolve run as the same user (the API binds per-user). |
| `Permission denied` reading `/opt/resolve/Developer/...` | Resolve installs root-owned; the module files are world-readable, but if your user cannot traverse `/opt/resolve`, run `sudo chmod o+x /opt/resolve /opt/resolve/Developer /opt/resolve/Developer/Scripting /opt/resolve/Developer/Scripting/Modules /opt/resolve/libs`. |
| First connection is slow / times out | Resolve spawns the scripting server lazily on first connect — it can take a few seconds. Retry once. |
| Script connects but no timeline appears | The project was created but the plan's asset paths weren't found on this machine (e.g. media on a different mount). Re-scan those paths first so MediaForge's stored paths match reality. |

## Notes

- The API is single-connection per Resolve instance — don't fire parallel
  exports; the backend serializes them.
- Export path: `POST /api/export/resolve` with `{"plan_id": "..."}` →
  `{"ok": true, "resolve": "v21.0.4", "project": "MediaForge-<plan>"}`.
  If Resolve is closed or scripting is disabled you get HTTP 503 with a
  message pointing here.
- If you don't use Resolve, use `POST /api/export/fcpxml` (FCPXML 1.10) or
  `POST /api/export/edl` (CMX3600) — both import into Resolve, Premiere,
  Final Cut, Kdenlive, Shotcut, and CapCut.

## Crash root cause (diagnosed 2026-09-20)

Resolve was not crashing for GPU or driver reasons — the RTX 3080, CUDA and
OpenGL 4.5 all initialise cleanly in `resolve_graphics_log.txt`.

The crash archive `~/.local/share/DaVinciResolve/logs/LogArchive/ResolveDebug_C1.txt`
contains **36,254 `IO.Audio` errors and 1 error of every other kind combined**,
all of the form:

```
IO.Audio | ERROR | Failed to decode clip <...IMG_1584.MOV>, track: 0 - Failed to decode the audio samples.
```

This box runs the **free** Resolve build (`/opt/resolve/DV_Resolve.png`; the
Studio install ships a different icon). The free Linux build licenses neither
the AAC audio decoder nor the consumer H.264/HEVC video decoders. Every clip in
`~/iphone-media` (806 files, 292 GB) carries an AAC track, so importing any of
them starts a decode-failure storm that eventually takes the app down.

`IMG_1584.MOV` is ProRes video + AAC audio: the *video* decoded fine, which is
why the failure looked random rather than codec-shaped.

### Fix

Condition clips before Resolve ever sees them:

```bash
python3 scripts/resolve_condition.py CLIP [CLIP...]          # DNxHR LB + PCM
python3 scripts/resolve_condition.py --quality edit CLIP     # DNxHR SQ
python3 scripts/resolve_condition.py --dry-run CLIP          # report only
```

Output lands in `data/resolve-ready/` and is content-cached (re-running is a
no-op). Also exposed over MCP as the `condition_media` tool.

**Condition only the clips an edit uses.** Conditioning expands files ~4x at
proxy quality and ~15x at finish quality; the 292 GB library would become
~1.2 TB / ~4.4 TB against ~100 GB of free disk.

The script strips `/opt/resolve/libs` out of `LD_LIBRARY_PATH` before spawning
ffmpeg — Resolve leaks that path globally and it makes every ffmpeg child exit 127.

### Still required: one GUI click

`preflight`/`export` still report `connected: false` until external scripting is
enabled. It defaults to off and is not present in `configs/config.dat` until set:
**Resolve → Preferences → System → General → "External scripting using" → Local**.

## Upgrading Resolve (21.0.4 -> 21.1, 2026-09-21)

Two things bite on Ubuntu 24.04:

1. **The installer's package check is wrong on 24.04 and is fatal.** `AppRun`'s
   `check_deb_packages()` runs `dpkg-query --show libapr1 libaprutil1 libasound2
   libglib2.0-0` — pre-`t64` names that Ubuntu 24.04 retired in the 64-bit
   `time_t` transition. They resolve to nothing, so the installer prints
   "Installation cancelled." and exits 1. The libraries themselves are all
   present under the `t64` package names. Bypass it:

   ```bash
   sudo -E SKIP_PACKAGE_CHECK=1 ./DaVinci_Resolve_21.1_Linux.run -i
   ```

2. **`sudo` alone cannot open the GUI.** X11 access control lists only
   `SI:localuser:bfam`, so the root-owned installer never connects and hangs
   with no window. `sudo -E` preserves `DISPLAY`/`XAUTHORITY` and root can read
   the user's Xauthority, so the cookie authenticates. Prefer that over
   `xhost +SI:localuser:root`, which weakens access control for every root process.

**The upgrade resets "External scripting using" to None** and replaces
`libs/Fusion/fusionscript.so`, so re-enable scripting after every upgrade.

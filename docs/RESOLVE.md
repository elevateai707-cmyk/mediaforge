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
| Python module | `/opt/resolve/apis/Scripting/Module/DaVinciResolveScript.py` |
| Native bridge lib | `/opt/resolve/libs/libfusionscript.so` |

The backend automatically retries the connection with these environment
variables set for its subprocess:

```bash
export LD_LIBRARY_PATH=/opt/resolve/libs:$LD_LIBRARY_PATH
export PYTHONPATH=/opt/resolve/apis/Scripting/Module:$PYTHONPATH
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
export PYTHONPATH=/opt/resolve/apis/Scripting/Module:$PYTHONPATH
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
| `ModuleNotFoundError: No module named 'DaVinciResolveScript'` | `PYTHONPATH` missing `/opt/resolve/apis/Scripting/Module`, or Resolve is not installed at `/opt/resolve` (that is the fixed install path on Linux). Verify the file exists: `ls /opt/resolve/apis/Scripting/Module/`. |
| `OSError: libfusionscript.so: cannot open shared object file` | `LD_LIBRARY_PATH=/opt/resolve/libs` not set when importing. |
| `resolve.scriptapp("Resolve")` returns `None` | Resolve is **not running**, or "External scripting using" is not set to **Network/Local**. Start Resolve, check Preferences → System → General, then retry. No restart needed after changing the setting. |
| Import works in a terminal but `/api/export/resolve` still 503s | The backend's 503 message echoes exactly this doc's fix. Also make sure the backend process and Resolve run as the same user (the API binds per-user). |
| `Permission denied` reading `/opt/resolve/apis/...` | Resolve installs root-owned; the module files are world-readable, but if your user cannot traverse `/opt/resolve`, run `sudo chmod o+x /opt/resolve /opt/resolve/apis /opt/resolve/apis/Scripting /opt/resolve/apis/Scripting/Module /opt/resolve/libs`. |
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

# Keeping MediaForge installations in sync

The shared source includes Creator Studio, the v2 editor, speech-timed styles,
original hook planning, Library uploads, watched folders and Resolve handoffs.
Code sync does not copy media, projects, API credentials, model weights or fonts.

## Confirm the running version

Open Settings → Version & editing capabilities on each computer. Compare the
version, source fingerprint and creative profile. A matching fingerprint means
matching backend/frontend source files. Restart the backend and rebuild the UI
after updating; an already-open tab may need a reload. Font warnings identify
missing families that would otherwise fall back to a different appearance.
Models and GPU availability appear in Settings. Provider credentials are local.
Same features do not imply equal render speed on different GPUs.

## Update an existing Linux checkout

Finish active imports/renders first. Record the installation folder and service
used by that computer. Back up its data directory (including mediaforge.db),
exports, .env, and local configuration while the app is stopped. Keep the backup
private. Do not overwrite or reset uncommitted work.

From that computer's checkout:

```bash
git status --short
git fetch origin
git switch feat/portable-creator-sync
git pull --ff-only origin feat/portable-creator-sync
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
backend/.venv/bin/python -m pip install fonttools
backend/.venv/bin/python scripts/install_style_fonts.py
```

Only continue past `git status` when the checkout is clean. If the branch already
exists with different local commits, stop at the failed fast-forward and merge
those changes deliberately. Never use reset --hard to synchronize a machine.
If Python/Node/FFmpeg are not installed, use the repo setup instructions first.
Optional cloud and MCP dependencies have their own requirements files; install
only those you use. Providers require per-machine credentials and can cost money.

Restart the service or launcher actually used by this installation. For the
repo's Linux user service this is `systemctl --user restart mediaforge.service`.
If the desktop launcher uses another checkout, update its target first. Open
`http://localhost:8420/`, refresh and compare Settings on both machines.
Reinstall the optional Resolve Utility from this checkout with
`backend/.venv/bin/python scripts/install_resolve_utility.py`.

Native Windows installation has not been validated. Under WSL use Linux paths
(e.g. `/mnt/c/Users/...`), Linux dependencies and the WSL instance's launcher.

## Keep adding footage

- Library → **Add photos & videos**: choose any additional batch from the device
  running your browser. Uploads copy media into this installation's data/uploads;
  identical files are detected by content hash. A failed file does not cancel
  the rest of the batch. Stay on the page until the batch completes.
- Library → **Watch folders**: in Settings add absolute paths on the computer
  running the backend, then **Save folders & scan**. Keep **Folder watcher** on.
  Files added later are imported while the app is running. Scan again after a
  restart to pick up files added while it was closed. External drives must be
  mounted at the saved path. The Library refreshes automatically.
- These are local libraries, not automatic synchronization between computers.
  A project refers to that installation's asset IDs and media paths.

## What needs separate setup

Comfy Cloud/ElevenLabs, local model downloads, Resolve, and fonts depend on the
machine. The external marketing/Comfy queue worker and Drive delivery are not
bundled into this app update. Saved voice references are recordings, not proof
of a successfully created voice clone. Reference recordings remain analysis-only
and are not included in this public repository.

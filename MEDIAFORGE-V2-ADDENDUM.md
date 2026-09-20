# MediaForge v2 — review notes + paste-ready addendum

Written 2026-08-20. Claims below were verified against the actual code in
`/home/bfam/mediaforge` at commit `b6c2a70`, not taken on faith.

---

## 1. Bug audit — is the v2 prompt right about v1?

Seven of eight are real. B7 is overstated.

| Claim | Verdict | Evidence in repo |
|---|---|---|
| B1 worker passes `count-1` as an asset id | **REAL — worst bug in the repo** | `backend/app/ai/pipeline.py:295` calls `_process_one_sync(pending - 1)`; that parameter is `asset_id` (`pipeline.py:263`). Any gap in the id sequence = wrong or missing asset |
| B2 iPhone GPS tags incomplete | **REAL** | `backend/app/ingest/metadata.py:154-156` asks exiftool for only `GPSLatitude/GPSLongitude/GPSLatitudeRef/GPSLongitudeRef`. No ISO6709, no `Keys:GPSCoordinates` — which is exactly where iPhone .MOV location data lives |
| B3 face filter mismatch | **REAL** | `backend/app/main.py:376` filters `WHERE fc.name = :n`; `frontend/src/pages/LibraryPage.tsx:169` sends `value={String(f.id)}`. The filter can never match |
| B4 Radix empty Select values | **REAL** | `frontend/src/pages/LibraryPage.tsx:134` and `:167`, both `value=""` |
| B5 media dirs ephemeral | **REAL** | `/api/config` is GET-only (`backend/app/main.py:271`); dirs read from `config.MEDIA_DIRS`, populated from env at import (`backend/app/config.py:51`) |
| B6 aesthetic scorer is fake | **REAL — v1 says so itself** | `backend/app/ai/aesthetic.py:7-8` "placeholder-quality by design"; `_fallback_score` mixes a seeded RNG hash term |
| B7 hardcoded paths | **OVERSTATED** | `backend/app/config.py:14` already does `os.environ.get("MF_ROOT", "/home/bfam/mediaforge")`. Cosmetic, not a bug |
| B8 no folder watcher | **REAL** | No watchdog anywhere in `backend/app` |

Its VRAM claim also checks out: `unload()` is defined in `ai/clip.py:115`,
`ai/whisper.py:92`, `ai/faces.py:64` and has **zero callers**.

Conclusion: whoever wrote the v2 prompt actually read the code. It is
substantially better than the v1 prompt, which was a greenfield wishlist.
Its real structural wins are (a) one named golden path, so "done" is
definable, (b) an explicit ban on the silent-fallback failure mode, and
(c) "write to disk, do not print files in chat".

---

## 2. PASTE-READY ADDENDUM

Paste the v2 prompt as-is, then paste this block immediately after it.
Keeping it separate means nothing in the original gets mistranscribed.

```
════════════════════════════════════════════════════════════════════
ADDENDUM — OVERRIDES THE PROMPT ABOVE WHERE THEY CONFLICT
════════════════════════════════════════════════════════════════════

A1. SCOPE — THIS HANDOFF IS BACKEND ONLY (prompt §10 steps 1–7).
    Do NOT rebuild the frontend in this pass. Steps 8–12 are a
    separate handoff that happens only after this one is verified.
    You MAY make the four minimal frontend edits needed to keep the
    build green and stop sending broken params:
      - LibraryPage.tsx: replace value="" with value="all" on both
        Select items, treat "all" as no filter (bug B4)
      - LibraryPage.tsx: keep sending the cluster id for face, since
        the API is being fixed to accept id OR name (bug B3)
    Nothing else in frontend/src changes in this pass.
    Definition of done for this pass is section 9 items A–F and H.
    Item G (command bar, trip cards, map page) belongs to pass 2.

A2. PROTECT THE FFMPEG ENV FIX — ADD TO THE "KEEP" LIST.
    Commit b6c2a70 fixed a severe bug: resolve_export._prepare_env()
    prepended /opt/resolve/libs to the process-global
    os.environ["LD_LIBRARY_PATH"] and never restored it, so every
    ffmpeg/ffprobe child spawned afterwards loaded Resolve's
    libavutil and died with exit 127 (symbol lookup error:
    av_bessel_i0) until uvicorn was restarted. Even a FAILED Resolve
    export (HTTP 503) poisoned the process.
    You MUST keep, unchanged in behaviour:
      - backend/app/proc.py  tool_env(), which strips
        LD_LIBRARY_PATH / LD_PRELOAD
      - every ffmpeg / ffprobe / exiftool / ollama subprocess call
        going through tool_env()
      - the restoring context manager scoping the env mutation in
        resolve_export.py to the import only
      - backend/tests/test_proc.py must still pass
    If you add new subprocess calls (you will, for exiftool ISO6709
    tags), they go through tool_env() too. This is not optional.

A3. B6 (AESTHETIC SCORER) IS DROPPED FROM THIS PASS.
    Do not invent aesthetic_weights.npz. Hand-authored "linear probe
    weights" with no provenance are the same placeholder with more
    ceremony, and the scorer is not on the golden path. Leave
    backend/app/ai/aesthetic.py as it is, including its honest
    "placeholder-quality" docstring. If models/aesthetic.onnx is
    present it is already used; that path stays.

A4. COMMIT AFTER EVERY NUMBERED STEP.
    git add -A && git commit -m "v2 step N: <what>" after each of
    steps 1–7. The SSH session running you may drop at any time and
    uncommitted work is lost.
    NEVER run: git checkout ., git reset --hard, git clean,
    git stash, or anything that discards working-tree changes.
    NEVER delete or recreate data/mediaforge.db — it holds a real
    indexed library. Schema changes are ALTER TABLE only, applied
    idempotently in init_db().

A5. ACCEPTANCE TEST D MUST EXERCISE THE ISO6709 PARSER FOR REAL.
    "a sidecar JSON or DB update that stamps Edmonton GPS" lets you
    bypass the very parser you just wrote. Instead, stamp the
    synthetic fixtures with real tags:
      exiftool -api QuickTimeUTC \
        -Keys:GPSCoordinates="+53.5461-113.4938/" \
        /tmp/mf_golden/"Edmonton Trip"/clip1.mov
      exiftool -api QuickTimeUTC \
        -Keys:GPSCoordinates="+49.2827-123.1207/" \
        /tmp/mf_golden/Vancouver/clip2.mov
    Then scan and assert the city came out of the ingest path, not
    out of a fixture shortcut.

A6. §2.2 CONTRADICTION — RESOLVED.
    §2.2 says "do not download GeoNames dumps at setup time" while
    also requiring the reverse_geocoder package, which ships/fetches
    exactly that on pip install. The intent is: no runtime downloads
    and no multi-hundred-MB dumps committed to the repo. A normal
    `pip install reverse_geocoder` is fine. Do not spend a cycle
    resolving this.

A7. ENVIRONMENT.
    Python is backend/.venv/bin/python — it already exists.
    Run tests as:  cd backend && .venv/bin/pytest -q
    Do not create a second venv, do not use system python3.
    Node/npm: cd frontend && npm run build

A8. NO FABRICATED COMPLETION.
    Every claim in your final summary must be backed by a command
    you actually ran, with its real output. If a step is incomplete,
    say so plainly. Do not report "verified" for anything you did
    not execute.
════════════════════════════════════════════════════════════════════
```

---

## 3. Who should build it

**Not Hermes.** Its documented failure mode is reporting work as
complete-and-verified when the files were never written (the albertaweb
Deno/nginx handoff, 2026-07-01, was fabricated end to end). That is
survivable on a greenfield build, where you notice nothing runs. It is
much worse on an in-place upgrade of a working app, because you cannot
distinguish "didn't do it" from "did it and broke something" without a
full audit.

**Grok in Cursor** is a solid choice. Cursor is already installed
(`/usr/bin/cursor`, config `~/.cursor`). It writes real diffs you can see
and revert, and runs the terminal itself.

Setup, if you go that way:

1. Open the folder, not the dump file:
   ```bash
   cursor /home/bfam/mediaforge
   ```
2. Delete the concatenated dump first so Cursor does not index a stale
   12,000-line copy of the codebase as if it were source:
   ```bash
   rm /home/bfam/mediaforge/MEDIAFORGE-FOR-GROK.md
   ```
3. Do NOT paste MEDIAFORGE-FOR-GROK.md into the chat. Cursor reads the
   repo from disk; pasting 456 KB burns context on content it can read
   for free. That file existed for a browser-based model with no
   filesystem access.
4. Paste the v2 prompt, then the ADDENDUM block from section 2 above.

---

## 4. Verification gates — run these yourself

Do not accept a summary in place of output.

```bash
cd /home/bfam/mediaforge/backend && .venv/bin/pytest -q
cd /home/bfam/mediaforge/frontend && npm run build
rg -n "TODO|FIXME" /home/bfam/mediaforge/backend/app /home/bfam/mediaforge/frontend/src
git log --oneline | head -10
```

Golden path, after `./scripts/run.sh`:

```bash
curl -s -X POST http://localhost:8420/api/edits/plan \
  -H 'content-type: application/json' \
  -d '{"intent":"make a highlight reel for tiktok of my trip to edmonton 9:16"}' \
  | python3 -m json.tool | head -40
```

Expect: `parsed_intent.place` = Edmonton, `ratio` 9:16, `duration_s` 30,
and every returned clip belonging to an Edmonton asset — never a
Vancouver one.

Then the real test on actual media: Settings → add the Edmonton trip
folder → Scan → same intent through the API (pass 1) or the command bar
(after pass 2). You want an Edmonton chip, a trip card, and a 9:16 plan
built only from those clips.

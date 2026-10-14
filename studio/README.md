# Elevate Vision Studio

Elevate Vision Digital's video editor: our build of
[FilmCraft](https://github.com/storytold/filmcraft), a pure-Rust, Premiere-style NLE with
its own codecs, by the ArtCraft team (MIT OR Apache-2.0).

MediaForge drafts the cut; Studio is where you finish it:

```
MediaForge plan ──studio_build / POST /api/export/filmcraft──► <plan>.fcproj ──► evstudio
 (agent or UI)        clips trimmed + placed, frame-filled,        (open, grade, title,
                      cross dissolves, styled captions             multicam, export)
                      (+ H.264 MP4 when the plan is approved)
```

## Install

| Machine | Command |
|---|---|
| Ubuntu (MediaForge box) | `./scripts/studio-setup.sh` (builds from source, 10–20 min the first time) |
| Windows desktop, prebuilt | Run **Actions ▸ Studio build** on GitHub, download `evstudio-windows-x64`, then `scripts\studio-setup.ps1 -ZipPath evstudio-windows-x64.zip` |
| Windows desktop, from source | `scripts\studio-setup.ps1` (installs Rust + MSVC Build Tools via winget if missing) |

Both put `evstudio` (the app) and `evstudio-cli` (headless) on your PATH and add a
launcher. MediaForge finds `evstudio-cli` automatically, or set `MF_FILMCRAFT_CLI`.
A stock `filmcraft-cli` also works.

Projects built on the Ubuntu box reference media by its Ubuntu path. To finish one on
Windows, open the same folders through a share and relink, or render the MP4 on Ubuntu
(`studio_build(render=true)`).

## How the rebrand works

`studio/brand.json` pins an exact upstream commit and holds every brand value. Its
`product`, `company`, `binary`, `cli` and `app_id` fields feed the rebrand, and its
links (`website`, `community`, `repo`, `issues`) point at **our GitHub repo until you
have a real site**. Set `website`/`community` to your domain when it's live.
`studio/rebrand.py` then patches a clean checkout:

- **ArtCraft marks removed**, as FilmCraft's `docs/brand/LICENSE-brand.txt` requires for
  modified versions. That covers the wordmark (replaced by ours), `docs/brand/`, the
  Discord/website links and the "ArtCraft family" wording.
- **Product name** in every UI/CLI string and in the translation catalogs (keys and
  values). Translations of the removed community labels fall back to English.
- **App ID** `digital.elevatevision.studio` and window name. Settings live apart from a
  stock FilmCraft install.
- **Binaries** `evstudio` / `evstudio-cli`, plus our icon set (`.png`, `.ico`, `.icns`,
  `.svg`) and NOTICE/README.
- **About box credit** in plain text: *"Based on FilmCraft by the ArtCraft team
  (MIT OR Apache-2.0)"*. The brand licence explicitly allows this, and the code licence
  requires keeping LICENSE/NOTICE.

The engine and the `.fcproj` format are untouched, so projects open in either app.
The script is strict: if an upstream change moves anything it patches, it stops with
a clear message instead of producing a half-branded build.

**Updating to a newer FilmCraft:** set `upstream.commit` (and `version`) in
`brand.json`, then re-run `scripts/studio-setup.sh` / the Actions build. If
`rebrand.py` stops, adjust the one snippet it names.

**Brand assets:** `python3 studio/make_brand_assets.py` regenerates `studio/assets/`
(icon and wordmark in MediaForge's amber/teal palette, set in Inter Bold under the OFL;
see `assets/OFL-Inter.txt`).

## Agents

- `studio_build` (mediaforge MCP) is the safe path. MediaForge generates every FilmCraft
  command from the plan, writes only to `exports/filmcraft/<plan>/`, and renders only
  approved plans.
- The raw `evstudio-cli mcp` server (cut, grade and drive the live app) is **opt-in**:
  `STUDIO_AGENT_MCP=1 ./scripts/studio-setup.sh` or `-RegisterAgentMcp`. FilmCraft
  documents that it has no folder sandbox, so it can read and write anything you can.
  Enable it only on a machine where that's acceptable.

## Security review of storytold/filmcraft (commit `cee1feb`, 2026-10-14)

| Area | Finding |
|---|---|
| Dependencies | 652 crates.io packages plus 1 same-org git dependency (`craft-libs`) pinned to a commit. No custom registries or `[patch]` overrides. |
| Build scripts | 3 `build.rs` files (fonts, i18n catalogs, Windows resources). No network access, no process spawning. |
| Network | Only `filmcraft-speech` (`ureq`), which downloads Whisper/Kokoro models. It's opt-in and off by default, pinned to a revision, and **SHA-256 + size verified**, written to a temp file then renamed. |
| Processes | `open`/`explorer`/`xdg-open` for "Reveal / Edit Original" (argument list, no shell). The `ffmpeg`/`ffprobe` calls are test-only. |
| Code hygiene | No bidi/zero-width characters, no encoded blobs, no committed binaries. `unsafe_code = "forbid"` everywhere except the platform crate. |
| CI | No `pull_request_target`; signing secrets live only in the release workflow. |
| Automation | The control channel (`evstudio --control PORT`) is loopback-only but **unauthenticated**: while it's on, any local program can drive the app. Nothing here enables it. MCP has **no folder sandbox**, so MediaForge doesn't expose it by default (see Agents). |

**Verdict: safe to build and use.** It's alpha software: upstream rates it ~56% "ready
for real work" against Premiere. Keep originals backed up and treat AI-built cuts as
drafts.

# Content batch 01 (2026-10-17)

The reference batch for the MediaForge content engine: 60 vertical (9:16) videos for two Elevate Vision Digital brands, AlbertaWeb.Design and @buildwithdb. Each brand gets 15 narrated videos (the owner's cloned voice, kinetic captions, drone B-roll) and 15 music-led drone B-roll videos.

- `SCRIPTS.md`: scripts, hooks, B-roll hints and post copy.
- `MUSIC_PLAN.md`: Suno track assignments, beat-sync rules and trending-audio guidance.
- `manifest.json`: generated from the two files above and editable by hand. The runner reads this file.
- `run_batch.py`: the runner. It uses only the standard library and can resume. Run `python3 -I run_batch.py --dry-run | --only ID | --redo ID`.

Rendered videos are not stored in git. They live outside the repo (`~/astra6/finished-content/batch-01/`) and in Google Drive under "Content - Batch 01".
The runner reads `MF_API_TOKEN` from `/home/g15/mediaforge/.env` at runtime. The token is never written anywhere else.
Music assignments and paths are specific to this batch. To start a new batch, copy this folder and edit it.

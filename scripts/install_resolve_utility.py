#!/usr/bin/env python3
"""Install/refresh Resolve's Workspace > Scripts > Utility > "MediaForge Apply Edit".

The installed file is generated from scripts/resolve_free.py with the repo root and
queue folder pinned, so it handles queued conform / assembly / apply_look items.
  scripts/install_resolve_utility.py            # writes the Utility script
  scripts/install_resolve_utility.py --print    # show it
"""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = Path.home() / ".local/share/DaVinciResolve/Fusion/Scripts/Utility/MediaForge Apply Edit.py"


def generate(root: Path = ROOT, queue: Path | None = None) -> str:
    src = (ROOT / "scripts" / "resolve_free.py").read_text()
    root_line = "ROOT=Path(os.environ.get('MF_ROOT') or Path(__file__).resolve().parents[1])"
    if root_line not in src or "QUEUE=ROOT/'data/resolve_queue'" not in src:
        raise RuntimeError("resolve_free.py no longer has the expected ROOT/QUEUE lines")
    src = src.replace(root_line, f"ROOT=Path({str(root)!r})")
    if queue is not None:
        src = src.replace("QUEUE=ROOT/'data/resolve_queue'", f"QUEUE=Path({str(queue)!r})")
    compile(src, "MediaForge Apply Edit.py", "exec")          # never install something that does not parse
    return src


def install(dest: Path = DEST) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(generate())
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args()
    print(generate() if args.print else f"installed {install()}")

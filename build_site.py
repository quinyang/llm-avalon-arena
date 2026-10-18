"""Build the static site in docs/ for GitHub Pages: the viewer plus a few hand-picked games.

    python build_site.py                          # uses FEATURED below
    python build_site.py --game transcripts/X.json "Title shown in the menu" --game ...

Then on GitHub: Settings > Pages > Source: Deploy from a branch > main, /docs.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"

# (transcript file, menu title). Edit freely; files must exist in transcripts/.
FEATURED = [
    ("20261017-201032_5ff60354.json", "Percival takes the bullet: Good wins, Merlin survives"),
    ("20261017-182845_bf161d62.json", "Merlin's first-turn slip: the Assassin finds him"),
    ("20261017-202152_24e15306.json", "Five-quest epic: 11 proposals, decided by the Assassin"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", nargs=2, action="append", metavar=("FILE", "TITLE"),
                    help="transcript path and menu title (repeatable); replaces FEATURED")
    args = ap.parse_args()
    games = [(Path(f).name, t) for f, t in args.game] if args.game else FEATURED

    out = DOCS / "transcripts"
    if DOCS.exists():
        shutil.rmtree(DOCS)
    out.mkdir(parents=True)
    shutil.copy(ROOT / "viewer.html", DOCS / "index.html")
    (DOCS / ".nojekyll").write_text("")  # serve files as-is
    if (ROOT / "analysis" / "summary.png").exists():
        shutil.copy(ROOT / "analysis" / "summary.png", DOCS / "results.png")

    index = []
    for name, title in games:
        src = ROOT / "transcripts" / name
        if not src.exists():
            raise SystemExit(f"missing transcript: {src}")
        t = json.loads(src.read_text())
        if not t.get("result"):
            raise SystemExit(f"{name} is not a finished game")
        shutil.copy(src, out / name)
        index.append({"file": name, "title": title})
    (out / "index.json").write_text(json.dumps({"games": index}, indent=2))
    print(f"built {DOCS}/ with {len(index)} games:")
    for g in index:
        print(f"  {g['title']}  ({g['file']})")


if __name__ == "__main__":
    main()

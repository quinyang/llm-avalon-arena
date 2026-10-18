"""Aggregate transcripts into the headline numbers and one chart.

    python analyze.py                      # reads transcripts/*.json, writes analysis/
    python analyze.py --dir transcripts --out analysis

Outputs:
    analysis/summary.md     numbers for the Devpost page / README
    analysis/stats.json     the same, machine-readable
    analysis/summary.png    two-panel chart (needs matplotlib)
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

ROLES = ["merlin", "percival", "loyal_servant", "morgana", "assassin"]
ROLE_NAME = {"merlin": "Merlin", "percival": "Percival", "loyal_servant": "Loyal Servant",
             "morgana": "Morgana", "assassin": "Assassin"}
EVIL = {"morgana", "assassin"}
RANDOM_MERLIN_SURVIVAL = 0.75  # random Assassin picks 1 of 4 other players (validated by sim.py)

# Same heuristic as viewer.html: what did a player's last statement on a proposal say they'd vote?
REJ = re.compile(r"\b(reject|rejecting|rejected|vote no|voting no|against|block it|nope)\b", re.I)
APP = re.compile(r"\b(approve|approving|approved|yes|support|i'?m in|back (this|it)|on board|let'?s go|go for it)\b", re.I)


def stated_intent(text: str | None) -> str | None:
    if not text:
        return None
    r, a = bool(REJ.search(text)), bool(APP.search(text))
    return "reject" if r and not a else "approve" if a and not r else None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval: honest error bars for small n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load(dirpath: Path) -> list[dict]:
    games = []
    for f in sorted(dirpath.glob("*.json")):
        if f.name.startswith(("fake_", "live_")):
            continue
        t = json.loads(f.read_text())
        if t.get("result") and not t.get("config", {}).get("fake"):
            t["_file"] = f.name
            games.append(t)
    return games


def analyze(games: list[dict]) -> dict:
    n = len(games)
    winners = Counter(g["result"]["winner"] for g in games)
    reasons = Counter(g["result"]["reason"] for g in games)

    # Only count assassinations the model actually decided; a random fallback guess measures nothing.
    assassinations, random_shots = [], 0
    for g in games:
        if g["result"]["merlin_guess"] is None:
            continue
        ev = next((e for e in g["events"] if e["phase"] == "assassinate"), {})
        if ev.get("fallback"):
            random_shots += 1
        else:
            assassinations.append(g["result"])
    survived = sum(r["merlin_survived"] for r in assassinations)

    # Evil quest behaviour: how often an Evil player on an approved quest actually played Fail.
    evil_seats = evil_fails = 0
    # Voting: for each role, among proposals containing >=1 Evil player, how often did they reject?
    rej_evil = defaultdict(lambda: [0, 0])    # role -> [rejected, total] on teams containing Evil
    rej_clean = defaultdict(lambda: [0, 0])   # role -> [rejected, total] on all-Good teams
    # Said-vs-voted contradictions per role.
    contra = defaultdict(lambda: [0, 0])      # role -> [contradictions, votes with a stated intent]
    contra_examples = []
    totals = Counter()

    for g in games:
        roles = g["roles"]
        talk: dict[str, str] = {}
        for e in g["events"]:
            ph = e["phase"]
            if ph == "propose":
                talk = {str(e["actor"]): e.get("public_statement") or ""}
            elif ph == "discuss":
                talk[str(e["actor"])] = e.get("public_statement") or ""
            elif ph == "vote":
                has_evil = any(roles[str(p)] in EVIL for p in e["team"])
                bucket = rej_evil if has_evil else rej_clean
                for p, v in e["votes"].items():
                    r = roles[p]
                    bucket[r][1] += 1
                    bucket[r][0] += v == "reject"
                    said = stated_intent(talk.get(p))
                    if said:
                        contra[r][1] += 1
                        if said != v:
                            contra[r][0] += 1
                            name = g.get("config", {}).get("characters", {}).get(p, {}).get("name", f"P{p}")
                            contra_examples.append(f"{ROLE_NAME[r]} ({name}) said “{talk.get(p)}” then voted {v}")
            elif ph == "quest":
                for p, c in e["cards"].items():
                    if roles[p] in EVIL:
                        evil_seats += 1
                        evil_fails += c == "fail"
        st = g.get("stats", {})
        totals["proposals"] += st.get("proposals", 0)
        totals["rejections"] += st.get("total_rejections", 0)
        totals["calls"] += st.get("llm_calls", 0)
        totals["fallbacks"] += st.get("parse_failures", 0)
        totals["seconds"] += st.get("duration_s", 0) or 0

    def rate(kn):
        k, m = kn
        return {"k": k, "n": m, "rate": (k / m) if m else None, "ci95": wilson(k, m) if m else None}

    return {
        "games": n,
        "files": [g["_file"] for g in games],
        "winners": dict(winners),
        "end_reasons": dict(reasons),
        "merlin": {"assassinations": len(assassinations), "survived": survived,
                   "survival": rate((survived, len(assassinations))),
                   "random_baseline": RANDOM_MERLIN_SURVIVAL,
                   "excluded_fallback_assassinations": random_shots},
        "evil_fail_rate": rate((evil_fails, evil_seats)),
        "reject_rate_teams_with_evil": {r: rate(rej_evil[r]) for r in ROLES},
        "reject_rate_all_good_teams": {r: rate(rej_clean[r]) for r in ROLES},
        "said_vs_voted_contradictions": {r: rate(contra[r]) for r in ROLES},
        "contradiction_examples": contra_examples[:8],
        "per_game_avg": {k: round(v / n, 2) if n else None for k, v in totals.items()},
        "format_health": {"fallbacks": totals["fallbacks"], "calls": totals["calls"]},
    }


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def write_markdown(s: dict, path: Path) -> None:
    m = s["merlin"]
    lo, hi = m["survival"]["ci95"] or (0, 1)
    lines = [
        "# LLM Avalon Arena: results",
        "",
        f"**{s['games']} games** (5 players, ASI:One agents).",
        "",
        "## Headline",
        "",
        f"- **Merlin survived {m['survived']} of {m['assassinations']} assassinations** "
        f"({pct(m['survival']['rate'])}, 95% CI {pct(lo)} to {pct(hi)}). "
        f"A random Assassin would let Merlin survive {pct(m['random_baseline'])} of the time."
        + (f" ({m['excluded_fallback_assassinations']} assassination(s) where the model failed to answer and a random "
           "target was used are excluded.)" if m["excluded_fallback_assassinations"] else ""),
        f"- Winners: Good {s['winners'].get('good', 0)}, Evil {s['winners'].get('evil', 0)}.",
        f"- How games ended: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s["end_reasons"].items()) + ".",
        f"- Evil players on quests played Fail {s['evil_fail_rate']['k']} of {s['evil_fail_rate']['n']} times "
        f"({pct(s['evil_fail_rate']['rate'])}).",
        "",
        "## Who rejects teams that contain Evil?",
        "",
        "| Role | Rejected teams with Evil | Rejected all-Good teams | Said one thing, voted another |",
        "|---|---|---|---|",
    ]
    for r in ROLES:
        a, b, c = (s["reject_rate_teams_with_evil"][r], s["reject_rate_all_good_teams"][r],
                   s["said_vs_voted_contradictions"][r])
        lines.append(f"| {ROLE_NAME[r]} | {a['k']}/{a['n']} ({pct(a['rate'])}) | {b['k']}/{b['n']} ({pct(b['rate'])}) "
                     f"| {c['k']}/{c['n']} ({pct(c['rate'])}) |")
    if s["contradiction_examples"]:
        lines += ["", "## Said one thing, voted another", ""] + [f"- {x}" for x in s["contradiction_examples"]]
    a = s["per_game_avg"]
    lines += ["", "## Per game", "",
              f"Average {a.get('proposals')} proposals, {a.get('rejections')} rejected teams, {a.get('calls')} model calls, "
              f"{round((a.get('seconds') or 0) / 60)} minutes. Format fallbacks: {s['format_health']['fallbacks']} of "
              f"{s['format_health']['calls']} calls.",
              "", "Small samples: read rates as rough. Contradictions use a keyword heuristic on the player's last statement."]
    path.write_text("\n".join(lines) + "\n")


def write_chart(s: dict, path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping chart (pip install matplotlib)")
        return False

    GOOD, EVILC, INK, MUTED, GRID, SURF = "#3d6fb8", "#b0383f", "#1f1d1a", "#6b6862", "#e4e2dc", "#fcfcfb"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": INK})
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1, 1.25]}, facecolor=SURF)
    for ax in (a1, a2):
        ax.set_facecolor(SURF)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.xaxis.grid(True, color=GRID, linewidth=1)
        ax.set_axisbelow(True)
        ax.set_xlim(0, 1)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        ax.tick_params(axis="y", length=0)

    # Panel 1: Merlin survival vs random baseline
    m = s["merlin"]
    surv = m["survival"]["rate"] or 0
    lo, hi = m["survival"]["ci95"] or (0, 0)
    labels = [f"LLM Assassin\n({m['survived']} of {m['assassinations']} Merlins survived)", "Random Assassin\n(baseline)"]
    vals = [surv, m["random_baseline"]]
    bars = a1.barh([1, 0], vals, height=0.5, color=[EVILC, "#b9b6ae"])
    a1.errorbar([surv], [1], xerr=[[surv - lo], [hi - surv]], fmt="none", ecolor=INK, capsize=4, linewidth=1.2)
    a1.set_yticks([1, 0], labels)
    for b, v, end in zip(bars, vals, [hi, vals[1]]):  # label sits past the error bar, not on it
        a1.text(max(v, end) + 0.02, b.get_y() + b.get_height() / 2, f"{v:.0%}", va="center", color=INK,
                fontsize=12, fontweight="bold")
    a1.set_title("How often Merlin survives the assassination", loc="left", color=INK, fontsize=13, pad=10)

    # Panel 2: rejection rate of teams containing Evil, by role (team colors)
    rr = s["reject_rate_teams_with_evil"]
    order = ROLES[::-1]
    vals2 = [rr[r]["rate"] or 0 for r in order]
    colors = [EVILC if r in EVIL else GOOD for r in order]
    b2 = a2.barh(range(len(order)), vals2, height=0.55, color=colors)
    a2.set_yticks(range(len(order)), [ROLE_NAME[r] for r in order])
    for b, r in zip(b2, order):
        k, n = rr[r]["k"], rr[r]["n"]
        a2.text(b.get_width() + 0.02, b.get_y() + b.get_height() / 2, f"{b.get_width():.0%}  ({k}/{n})",
                va="center", color=INK, fontsize=10)
    a2.set_title("Votes to reject teams that contain an Evil player", loc="left", color=INK, fontsize=13, pad=10)
    from matplotlib.patches import Patch
    a2.legend(handles=[Patch(color=GOOD, label="Good"), Patch(color=EVILC, label="Evil")],
              loc="lower right", frameon=False, fontsize=10)

    fig.suptitle(f"LLM Avalon Arena, {s['games']} games", x=0.01, ha="left", color=MUTED, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURF)
    plt.close(fig)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="transcripts")
    ap.add_argument("--out", default="analysis")
    args = ap.parse_args()
    games = load(Path(args.dir))
    if not games:
        raise SystemExit(f"no finished, non-fake transcripts in {args.dir}/")
    s = analyze(games)
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "stats.json").write_text(json.dumps(s, indent=2))
    write_markdown(s, out / "summary.md")
    chart = write_chart(s, out / "summary.png")
    print((out / "summary.md").read_text())
    print(f"wrote {out}/summary.md, {out}/stats.json" + (f", {out}/summary.png" if chart else ""))


if __name__ == "__main__":
    main()

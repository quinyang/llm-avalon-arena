# LLM Avalon Arena: results

**13 games** (5 players, ASI:One agents).

## Headline

- **Merlin survived 3 of 13 assassinations** (23%, 95% CI 8% to 50%). A random Assassin would let Merlin survive 75% of the time.
- Winners: Good 3, Evil 10.
- How games ended: merlin assassinated 10, three quests succeeded 3.
- Evil players on quests played Fail 10 of 33 times (30%).

## Who rejects teams that contain Evil?

| Role | Rejected teams with Evil | Rejected all-Good teams | Said one thing, voted another |
|---|---|---|---|
| Merlin | 41/49 (84%) | 0/24 (0%) | 4/42 (10%) |
| Percival | 26/49 (53%) | 3/24 (12%) | 9/48 (19%) |
| Loyal Servant | 22/49 (45%) | 7/24 (29%) | 2/41 (5%) |
| Morgana | 8/49 (16%) | 12/24 (50%) | 4/49 (8%) |
| Assassin | 8/49 (16%) | 18/24 (75%) | 9/52 (17%) |

## Said one thing, voted another

- Assassin (P0) said “P3 skipped both me and P1. Convenient. Reject.” then voted approve
- Percival (P2) said “P3's proposal has all the vision of a mid-tier merger. I'll approve, but I'm billing overtime if this quest goes south.” then voted reject
- Percival (P2) said “Another pitch with P0 and me. Derivative, but the risk-reward works—he flinches, we get intel. Approved.” then voted reject
- Merlin (P2) said “One clean quest doesn't clear anyone. P0 backed P4's self-pick last round, and now we're expanding that same core? I don't know, I just... I think I need to reject this. We need more diverse data points before I feel...” then voted approve
- Morgana (P2) said “I backed Q2.2 for intel, not trust. We got our fail. P0 and I are untouched. Rejecting fresh blood to recycle suspects? Evil's playbook.” then voted approve
- Merlin (P3) said “P0, you claimed clean last round. Now rejecting your own spot? P2's first-ever rejection conveniently lands when neither P2 nor P4 are on the team. Chuckle.” then voted approve
- Percival (P3) said “P1, your pitch is 'we had one good quarter, so now we're hiring.' Zero due diligence on P2 beyond polite enthusiasm. I'll approve, but I'm auditing this ledger.” then voted reject
- Percival (P3) said “'Unproven' is a polite way to say ' we're all suspects,' but 50/50 is better than stuck. Let's go. No over analysis needed.” then voted reject

## Per game

Average 5.62 proposals, 1.85 rejected teams, 72.0 model calls, 42 minutes. Format fallbacks: 10 of 936 calls.

Small samples: read rates as rough. Contradictions use a keyword heuristic on the player's last statement.

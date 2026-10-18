# LLM Avalon Arena: results

**6 games** (5 players, ASI:One agents).

## Headline

- **Merlin survived 1 of 6 assassinations** (17%, 95% CI 3% to 56%). A random Assassin would let Merlin survive 75% of the time.
- Winners: Good 1, Evil 5.
- How games ended: merlin assassinated 5, three quests succeeded 1.
- Evil players on quests played Fail 2 of 11 times (18%).

## Who rejects teams that contain Evil?

| Role | Rejected teams with Evil | Rejected all-Good teams | Said one thing, voted another |
|---|---|---|---|
| Merlin | 13/14 (93%) | 0/10 (0%) | 2/11 (18%) |
| Percival | 6/14 (43%) | 1/10 (10%) | 4/14 (29%) |
| Loyal Servant | 5/14 (36%) | 1/10 (10%) | 0/17 (0%) |
| Morgana | 2/14 (14%) | 2/10 (20%) | 2/15 (13%) |
| Assassin | 4/14 (29%) | 6/10 (60%) | 1/14 (7%) |

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

Average 4.0 proposals, 0.67 rejected teams, 50.5 model calls, 26 minutes. Format fallbacks: 2 of 303 calls.

Small samples: read rates as rough. Contradictions use a keyword heuristic on the player's last statement.

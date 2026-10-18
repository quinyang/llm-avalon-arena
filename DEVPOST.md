# LLM Avalon Arena

## Inspiration

Most agent demos are one agent doing one task. I wanted agents reasoning about *each other*, with secrets. Avalon is the cleanest test: Merlin knows who the traitors are, but if the Assassin spots him at the end, Evil wins anyway. Being right isn't enough. You have to be right quietly.

## What it does

Five AI agents, each dealt a random Arthurian character, play full games of *The Resistance: Avalon*: secret roles, team proposals, simultaneous votes, hidden quest cards, and the final assassination.

Every move produces two things, stored separately: what the agent privately thinks and what it says out loud. A round-table viewer replays any game or follows one live. Turn on **Show secrets** and you see each player's real role and their private reasoning next to their public words, so you can watch an agent decide to lie and then lie. The viewer also flags players whose vote contradicts what they just said.

## How we built it

- **Rules engine first.** A pure Python state machine with zero model calls, checked against 1,000 random-bot games before spending anything on inference.
- **Agents see a table, not a chat log.** Each prompt gets a compact summary of quests, proposals and votes. Agents answer through forced function calls, validated, with one retry and a random legal move as a last resort.
- **Fetch.ai ASI:One** (`asi1-fast`) powers every player through an OpenAI-compatible adapter.
- **Viewer** is one static HTML file with SVG, no framework and no build step.

## Challenges we ran into

- We had no Anthropic credits, so we switched to ASI:One mid-hackathon and wrote an adapter for it.
- ASI spends about 5,000 hidden reasoning tokens per move, so early answers got cut off before the move was written. Raising the budget and adding a JSON fallback took format failures down to 10 in 936 calls.
- Each game takes 15 to 50 minutes, so we ran games in parallel and built a live view.
- Merlin kept giving himself away. Rather than hide it, we made it the headline metric.

## Accomplishments that we're proud of

- **Merlin survived only 3 of 13 assassinations** (95% range 8% to 50%). A random Assassin would miss him 75% of the time.
- **Merlin voted against 41 of 49 teams that contained an Evil player, and 0 of 24 all-Good teams.** His speeches were careful; his votes told the Assassin everything.
- **When Good did win, Percival usually took the bullet.** In 2 of the 3 Good wins, the Assassin shot Percival, who had been voting like someone with perfect information to draw fire. In one game the Assassin even read the real Merlin's matching votes as "Percival shadowing Merlin."
- A complete, correct Avalon with all five roles, hidden information, the five-rejection rule and the assassination endgame, validated before a single model call.

## What we learned

LLMs are far better at deduction than concealment. Give an agent the answer and it will act on it every time, even when acting on it loses the game. Evil figured out the counter on its own: let Good pass quests, then name the player whose votes were never wrong.

## What's next for LLM Avalon Arena

- Teach Merlin to spend a few votes on cover and measure whether survival goes up.
- A judge model that scores each statement as honest, concealing or lying, per role.
- Model match-ups, such as a stronger Assassin against a weaker Merlin.
- A human seat so people can play against the agents.
- The 7-player game with Mordred and Oberon.

## Built with

python, fetch-ai, asi-one, openai-api, llm, multi-agent-systems, prompt-engineering, game-theory, html5, javascript, svg, matplotlib

---

*Avalon is designed by Don Eskridge and published by Indie Boards & Cards. This is a non-commercial hackathon project using the game's public rules.*

---
name: vox-populi
description: Generate a one-person-one-vote view of any Polymarket multi-outcome event, filtering wallets by position size and reporting Yes/No voter splits per outcome.
version: 1.0.0
license: MIT
author: Daniel Cazzulino
tags:
  - polymarket
  - prediction-markets
  - crypto
  - finance
  - market-research
  - sentiment-analysis
  - data-analysis
  - python
keywords:
  - one person one vote
  - retail sentiment
  - popular vote
  - whale filtering
  - election markets
  - yes no split
---

# Vox Populi Skill

**Vox Populi** creates a **retail / popular vote** view of Polymarket events.

It filters out whales by current position size and shows **unique qualifying voters** with a **Yes % / No %** split for each active outcome.

## When to Use

- You want to see what the broader crowd thinks, not just large positions
- You want a **people-weighted** companion to Polymarket's **money-weighted** pricing
- You want to compare retail sentiment across multi-outcome prediction markets
- You want structured JSON that an agent can summarize, rank, or render as a table

## Human-in-the-Loop

You must not invent missing inputs when there are meaningful trade-offs.

If the user has not provided a Polymarket event or URL, ask for it. If the user has not specified a wallet-size band, use the skill defaults of **$10 minimum** and **$100 maximum** unless they ask for a different range.

If an ask/follow-up tool exists in the current environment, prefer that tool over free-form questions.

This skill should remain compatible with GitHub Copilot, Codex, Cursor, Claude Code, Cline, Roo, and similar SKILL.md-aware agents.

## How to Use

Run the bundled Python script non-interactively.

The Python entrypoint is **`scripts/vox_populi.py` located next to this SKILL.md file**. Do **not** assume the current working directory is the installed skill directory.

When invoking the script:

1. Prefer a path resolved from the directory containing this `SKILL.md`.
2. If your agent runs commands from the project root, use the installed skill path for that agent.
3. For agents using the shared project convention, that path is typically `.agents/skills/vox-populi/scripts/vox_populi.py`.

Examples:

```bash
python "<skill-dir>/scripts/vox_populi.py" argentina-presidential-election-winner --min-usd 10 --max-usd 100
```

```bash
python "<skill-dir>/scripts/vox_populi.py" https://polymarket.com/event/argentina-presidential-election-winner --print-table
```

Required input:

- an event slug or full Polymarket event URL
- `--min-usd` default minimum qualifying position size (default to **10**)
- `--max-usd` default maximum qualifying position size (default to **100**)

Optional input:

- `--output` to choose the directory where the JSON file is written
- `--print-table` to also emit the CLI table to **stderr**

Important runtime behavior:

- the script writes progress, warnings, and errors to **stderr**
- the script prints the generated JSON file path to **stdout**
- the JSON file is the source of truth for downstream analysis or custom rendering

## Returned JSON data

The script prints the path to a JSON file with this top-level shape:

```json
{
  "event_title": "Argentina Presidential Election Winner",
  "event_slug": "argentina-presidential-election-winner",
  "filter_min_usd": 10.0,
  "filter_max_usd": 100.0,
  "total_voters": 84,
  "outcomes": [
    {
      "name": "Javier Milei",
      "voters": 56,
      "yes_voters": 46,
      "no_voters": 11,
      "yes_price": 47.5,
      "no_price": 52.5,
      "popular_pct": 66.7,
      "yes_pct": 82.1,
      "no_pct": 19.6
    }
  ],
  "timestamp": "2026-05-30T01:46:44.087429"
}
```

Field meanings:

- `event_title`: display title from Polymarket
- `event_slug`: normalized event slug used for the request
- `filter_min_usd` / `filter_max_usd`: inclusive position-value filter used for qualifying voters
- `total_voters`: count of unique qualifying wallets across all active outcomes
- `outcomes`: active outcomes sorted by `popular_pct` descending
- `timestamp`: ISO-8601 snapshot time

Each `outcomes[]` item contains:

- `name`: outcome or candidate name
- `voters`: unique qualifying wallets in either Yes or No for that outcome
- `yes_voters` / `no_voters`: unique qualifying wallets per side
- `yes_price` / `no_price`: current market prices in percent
- `popular_pct`: share of total qualifying voters attributed to that outcome
- `yes_pct` / `no_pct`: split within that outcome's voter set

`popular_pct` values can sum to more than **100%** because one wallet can qualify in multiple outcomes.

## Table rendering for the agent

If you present the results as a table, render it from the JSON using the same layout as `render_cli_table`.

Columns:

- `RANK`
- `OUTCOME`
- `POP %`
- `VOTERS`
- `YES %`
- `NO %`
- `MKT YES`
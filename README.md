# Vox Populi

[![skills.sh](https://skills.sh/b/devlooped/vox-populi)](https://skills.sh/devlooped/vox-populi)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Vox Populi** is a skill for AI coding agents that turns a Polymarket event into a **people-weighted view** instead of a money-weighted one.

Install the skill, then ask your agent questions like **"vox populi SpaceX IPO"** or **"run vox populi on the Argentina election market with a $10-$100 wallet band"**. The agent will fetch the market, filter out whales by position size, and return a table showing which outcomes have the broadest retail support.

## Install

```bash
npx skills add devlooped/vox-populi
```

After installation, restart your agent session if it does not pick up newly added skills automatically.

## What it feels like to use

This repo is not meant to be read like a Python package. The normal flow is:

1. Install the skill with `npx skills add devlooped/vox-populi`
2. Open your agent of choice
3. Prompt it naturally
4. Get back a ranked table and structured snapshot of retail sentiment

Supported agent environments include tools that understand `SKILL.md`, such as **GitHub Copilot**, **Codex**, **Cursor**, **Claude Code**, **Cline**, and **Roo**.

## Prompt examples

Use natural prompts. You do not need to know the script path or command-line arguments.

### Simple

```text
vox populi SpaceX IPO
```

### With a wallet-size filter

```text
Run vox populi for the Argentina presidential election winner market using wallets between $10 and $100.
```

### With a Polymarket URL

```text
Use vox populi on https://polymarket.com/event/argentina-presidential-election-winner and show me the top outcomes by popular support.
```

### Comparative analysis

```text
Use vox populi on the current Fed decision market and explain where retail sentiment differs from market pricing.
```

## Example response

For a prompt like:

```text
vox populi SpaceX IPO
```

an agent can respond with something like:

```text
EVENT: SpaceX IPO in 2026?
FILTER: Position size $10 - $100 USD | Total qualifying voters: 1,284

RANK  | OUTCOME                |   POP % |  VOTERS |  YES % | UNPOP % |   NO %
-----------------------------------------------------------------------------
1     | Yes                    |   61.4% |     788 |   73.2% |   26.6% |  28.0%
2     | No                     |   46.8% |     601 |   39.3% |   30.0% |  63.9%

Last updated: 2026-05-30 03:20:11
```

The exact market title, outcomes, and counts depend on live Polymarket data.

## What the agent is doing for you

When invoked, the skill:

- resolves the requested Polymarket event from a slug or URL
- filters wallets by current position size, defaulting to **$10-$100**
- counts unique qualifying wallets instead of dollar exposure
- in Yes and No independently, keeps only each wallet's largest current position
- reports **popular support** plus the **Yes/No split** for each outcome

This makes it useful for prompts about **retail sentiment**, **crowd conviction**, **prediction markets**, **whale filtering**, and **people-vs-money divergence**.

## Good prompt patterns

These tend to work well:

- `vox populi <market name>`
- `use vox populi on <polymarket url>`
- `run vox populi with min $25 max $250`
- `compare vox populi support with the market odds`
- `summarize retail sentiment for this prediction market`

## Notes

- Results are based on live public Polymarket APIs.
- If your prompt is ambiguous, the agent may ask you which market you mean.
- The same wallet can appear across multiple outcomes, so popular-share percentages may add up to more than 100%.

## For developers

If you are looking for the agent-facing instructions or implementation details, start with:

- `SKILL.md` for agent behavior
- `scripts/vox_populi.py` for the bundled CLI

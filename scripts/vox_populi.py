#!/usr/bin/env python3
"""
Vox Populi — Polymarket Popular Vote CLI
One person, one vote view with Yes/No split, filtered by position size.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Set
from urllib.parse import urlparse

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://polymarket.com/",
}
MIN_POPULAR_PCT_TO_DISPLAY = 1.0


def normalize_event_slug(value: str) -> str:
    """Accept either an event slug or a full Polymarket event URL."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("Event slug or URL cannot be empty.")

    if "://" not in candidate:
        return candidate.strip("/")

    parsed = urlparse(candidate)
    if not parsed.netloc.endswith("polymarket.com"):
        raise ValueError("Only polymarket.com event URLs are supported.")

    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("Could not extract an event slug from the provided URL.")

    if "event" in parts:
        event_index = parts.index("event")
        if event_index + 1 < len(parts):
            return parts[event_index + 1]

    return parts[-1]


def get_event(slug: str) -> Dict[str, Any]:
    """Fetch event details from Gamma API."""
    url = f"{GAMMA_API}/events"
    resp = requests.get(url, params={"slug": slug}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    events = resp.json()
    if not events:
        raise ValueError(f"No event found for slug: {slug}")
    return events[0]


def parse_json_list(value: Any, default: List[str]) -> List[str]:
    """Polymarket sometimes returns JSON arrays as strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return default
        return parsed if isinstance(parsed, list) else default
    return default


def get_market_positions(
    condition_id: str,
    status: str = "OPEN",
    page_size: int = 500,
    max_offset: int = 10000,
) -> List[Dict[str, Any]]:
    """Fetch all open positions for a market using the current market-positions API."""
    all_positions: List[Dict[str, Any]] = []
    offset = 0
    url = f"{DATA_API}/v1/market-positions"

    while True:
        params = {
            "market": condition_id,
            "status": status,
            "limit": page_size,
            "offset": offset,
            "sortBy": "TOKENS",
            "sortDirection": "DESC",
        }
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        buckets = resp.json()
        if not buckets or not isinstance(buckets, list):
            break

        batch_count = 0
        max_bucket_size = 0
        for bucket in buckets:
            positions = bucket.get("positions", [])
            max_bucket_size = max(max_bucket_size, len(positions))
            all_positions.extend(positions)
            batch_count += len(positions)

        if batch_count == 0 or max_bucket_size < page_size:
            break

        offset += page_size
        if offset > max_offset:
            break
        time.sleep(0.1)

    return all_positions


def market_name(market: Dict[str, Any]) -> str:
    candidate = str(market.get("groupItemTitle") or "").strip()
    if candidate:
        return candidate
    question = str(market.get("question") or "Unknown").strip()
    return question.removeprefix("Will ").removesuffix(" win?").strip()


def validate_filter_range(min_usd: float | None, max_usd: float | None) -> None:
    if (min_usd is not None and min_usd < 0) or (max_usd is not None and max_usd < 0):
        raise ValueError("Filter range must be non-negative.")
    if min_usd is not None and max_usd is not None and min_usd > max_usd:
        raise ValueError("Minimum USD filter cannot be greater than maximum USD filter.")


def in_filter_range(value_usd: float, min_usd: float | None, max_usd: float | None) -> bool:
    if min_usd is not None and value_usd < min_usd:
        return False
    if max_usd is not None and value_usd > max_usd:
        return False
    return True


def format_filter_label(min_usd: float | None, max_usd: float | None) -> str:
    if min_usd is None and max_usd is None:
        return "unbounded"
    if min_usd is None and max_usd is not None:
        return f"<= ${max_usd:,} USD"
    if min_usd is not None and max_usd is None:
        return f">= ${min_usd:,} USD"
    return f"${min_usd:,} - ${max_usd:,} USD"


def get_output_file_path(event_slug: str, output_dir: str | None) -> Path:
    """Build the output file path, overwriting the daily file if it exists."""
    if output_dir:
        base_dir = Path(output_dir).expanduser()
    else:
        base_dir = Path(tempfile.gettempdir()) / "polymarket"
    base_dir.mkdir(parents=True, exist_ok=True)
    date_part = datetime.now().strftime("%Y-%m-%d")
    return (base_dir / f"{event_slug}-{date_part}.json").resolve()


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def extract_wallet(position: Dict[str, Any]) -> str | None:
    wallet = str(position.get("proxyWallet") or "").strip()
    return wallet or None


def filter_wallets(
    positions: List[Dict[str, Any]],
    min_usd: float | None,
    max_usd: float | None,
    outcome_name: str,
    side: str,
) -> Set[str]:
    top_position_by_wallet: Dict[str, float] = {}
    skipped_wallets = 0

    for holder in positions:
        wallet = extract_wallet(holder)
        if wallet is None:
            skipped_wallets += 1
            continue

        value_usd = float(holder.get("currentValue", 0) or 0)
        current_top = top_position_by_wallet.get(wallet)
        if current_top is None or value_usd > current_top:
            top_position_by_wallet[wallet] = value_usd

    wallets = {
        wallet
        for wallet, value_usd in top_position_by_wallet.items()
        if in_filter_range(value_usd, min_usd, max_usd)
    }

    if skipped_wallets:
        print(
            (
                f"  Warning: skipped {skipped_wallets} {side} position(s) without a "
                f"proxyWallet for {outcome_name}."
            ),
            file=sys.stderr,
        )

    return wallets


def fetch_vox_populi(
    event_slug_or_url: str,
    min_usd: float | None = None,
    max_usd: float | None = None,
) -> Dict[str, Any]:
    """Fetch event data and calculate popular vote with Yes/No split."""
    event_slug = normalize_event_slug(event_slug_or_url)
    print(f"Fetching event: {event_slug}...", file=sys.stderr)
    event = get_event(event_slug)

    # Collect raw positions per wallet per outcome per side (taking max value per wallet+outcome+side
    # in case the API returns multiple rows for the same thing). We do *not* apply the min/max filter yet.
    outcome_info: Dict[str, Dict[str, Any]] = {}
    wallet_yes: Dict[str, Dict[str, float]] = defaultdict(dict)  # wallet -> {outcome_name: max currentValue for Yes on it}
    wallet_no: Dict[str, Dict[str, float]] = defaultdict(dict)
    skipped_positions = 0

    for market in event.get("markets", []):
        condition_id = market.get("conditionId")
        if not condition_id:
            continue
        outcome_name = market_name(market)

        # Only consider markets that have real trading activity.
        # The Gamma API often returns many placeholder/stub markets ("Person B", "Other", etc.)
        # with volume=0 and liquidity=0 that do not appear on the public event page.
        # These stubs can still have ghost 0-value records in the positions API.
        volume = market.get("volumeNum") or market.get("volume")
        if volume is not None and float(volume) <= 0:
            continue

        outcome_prices = parse_json_list(market.get("outcomePrices"), ["0.5", "0.5"])
        yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
        no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5

        print(f"  Processing: {outcome_name}...", file=sys.stderr)

        outcome_info[outcome_name] = {
            "yes_price": round(yes_price * 100, 1),
            "no_price": round(no_price * 100, 1),
        }

        positions = get_market_positions(str(condition_id))
        for pos in positions:
            wallet = extract_wallet(pos)
            if wallet is None:
                skipped_positions += 1
                continue
            side = pos.get("outcome")
            value_usd = float(pos.get("currentValue", 0) or 0)

            if value_usd == 0:
                # Ignore zero present-value (ghost/legacy/resolved) records.
                # They should not contribute to "top position" for popular vote.
                continue

            if side == "Yes":
                d = wallet_yes[wallet]
                if outcome_name not in d or value_usd > d[outcome_name]:
                    d[outcome_name] = value_usd
            elif side == "No":
                d = wallet_no[wallet]
                if outcome_name not in d or value_usd > d[outcome_name]:
                    d[outcome_name] = value_usd

    if skipped_positions:
        print(
            f"  Warning: skipped {skipped_positions} position(s) without a proxyWallet.",
            file=sys.stderr,
        )

    # Global per-wallet top position (by currentValue) across *all* outcomes for the Yes side,
    # and independently for the No side. Apply the min/max filter to the *top* value only.
    # This guarantees each wallet contributes at most one Yes vote and at most one No vote
    # to the entire event (awarded to the outcome of their highest-value position on that side).
    yes_for_outcome: Dict[str, Set[str]] = defaultdict(set)
    no_for_outcome: Dict[str, Set[str]] = defaultdict(set)
    qualifying_wallets: Set[str] = set()

    for wallet, out_to_val in wallet_yes.items():
        if not out_to_val:
            continue
        best_outcome, best_val = max(out_to_val.items(), key=lambda item: item[1])
        if in_filter_range(best_val, min_usd, max_usd):
            yes_for_outcome[best_outcome].add(wallet)
            qualifying_wallets.add(wallet)

    for wallet, out_to_val in wallet_no.items():
        if not out_to_val:
            continue
        best_outcome, best_val = max(out_to_val.items(), key=lambda item: item[1])
        if in_filter_range(best_val, min_usd, max_usd):
            no_for_outcome[best_outcome].add(wallet)
            qualifying_wallets.add(wallet)

    total_voters = len(qualifying_wallets)

    # Build per-outcome stats. "yes_voters" now means wallets for whom this outcome had their single
    # highest-value Yes position (and it passed the filter). Same for no_voters.
    outcomes_data: List[Dict[str, Any]] = []
    for name, info in outcome_info.items():
        y_set = yes_for_outcome.get(name, set())
        n_set = no_for_outcome.get(name, set())
        outcomes_data.append(
            {
                "name": name,
                "voters": len(y_set | n_set),
                "yes_voters": len(y_set),
                "no_voters": len(n_set),
                "yes_price": info["yes_price"],
                "no_price": info["no_price"],
            }
        )

    for outcome in outcomes_data:
        if total_voters > 0:
            outcome["popular_pct"] = round(
                (outcome["yes_voters"] / total_voters) * 100, 1
            )
        else:
            outcome["popular_pct"] = 0.0

        if total_voters > 0:
            outcome["unpopular_pct"] = round(
                (outcome["no_voters"] / total_voters) * 100, 1
            )
        else:
            outcome["unpopular_pct"] = 0.0

        outcome_vote_count = outcome["yes_voters"] + outcome["no_voters"]
        if outcome_vote_count > 0:
            outcome["yes_pct"] = round(
                (outcome["yes_voters"] / outcome_vote_count) * 100, 1
            )
            outcome["no_pct"] = round(
                (outcome["no_voters"] / outcome_vote_count) * 100, 1
            )
        else:
            outcome["yes_pct"] = 0.0
            outcome["no_pct"] = 0.0

    active_outcomes = [
        outcome
        for outcome in outcomes_data
        if (
            outcome["voters"] > 0
            and outcome["popular_pct"] >= MIN_POPULAR_PCT_TO_DISPLAY
            and outcome["yes_price"] > 0
        )
    ]
    active_outcomes.sort(key=lambda item: item["popular_pct"], reverse=True)

    return {
        "title": event.get("title", event_slug),
        "slug": event_slug,
        "min_usd": min_usd,
        "max_usd": max_usd,
        "total_voters": total_voters,
        "outcomes": active_outcomes,
        "timestamp": datetime.now().isoformat(),
    }


def render_cli_table(data: Dict[str, Any]) -> str:
    """Render the CLI table with Yes/No split."""
    lines = [
        "",
        f"EVENT: {data['title']}",
        (
            f"FILTER: Position size {format_filter_label(data['min_usd'], data['max_usd'])} "
            f"| Total qualifying voters: "
            f"{data['total_voters']:,}"
        ),
        "",
        (
            f"{'RANK':<5} | {'OUTCOME':<22} | {'MKT YES':>7} | {'POP':>7} | "
            f"{'VOTES':>7} | {'YES':>6} | {'UNPOP':>8} | {'VOTES':>7} | {'NO':>6}"
        ),
        "-" * 101,
    ]

    for index, outcome in enumerate(data["outcomes"], 1):
        lines.append(
            f"{index:<5} | {outcome['name']:<22} | "
            f"{outcome['yes_price']:>6.1f}% | {outcome['popular_pct']:>6.1f}% | "
            f"{outcome['yes_voters']:>7,} | {outcome['yes_pct']:>5.1f}% | "
            f"{outcome['unpopular_pct']:>7.1f}% | {outcome['no_voters']:>7,} | "
            f"{outcome['no_pct']:>5.1f}%"
        )

    lines.extend(
        [
            "",
            f"Last updated: {data['timestamp'][:19].replace('T', ' ')}",
        ]
    )
    return "\n".join(lines)


def print_cli_table(data: Dict[str, Any]) -> None:
    print(render_cli_table(data), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vox Populi - Polymarket one person, one vote view with Yes/No split"
    )
    parser.add_argument(
        "event",
        help="Event slug or full Polymarket event URL (for example argentina-presidential-election-winner)",
    )
    parser.add_argument(
        "--min-usd",
        type=float,
        default=None,
        help="Minimum position value USD filter. Omit for no lower bound.",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=None,
        help="Maximum position value USD filter. Omit for no upper bound.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Directory to save the output JSON. Default: temporary polymarket directory",
    )
    parser.add_argument(
        "--print-table",
        action="store_true",
        help="Also render the CLI table to stderr after writing the JSON file",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        validate_filter_range(args.min_usd, args.max_usd)
        data = fetch_vox_populi(
            event_slug_or_url=args.event,
            min_usd=args.min_usd,
            max_usd=args.max_usd,
        )
        output_file = get_output_file_path(data["slug"], args.output)
        write_json(output_file, data)
        if args.print_table:
            print_cli_table(data)
        print(output_file)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

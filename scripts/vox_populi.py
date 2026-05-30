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


def validate_filter_range(min_usd: float, max_usd: float) -> None:
    if min_usd < 0 or max_usd < 0:
        raise ValueError("Filter range must be non-negative.")
    if min_usd > max_usd:
        raise ValueError("Minimum USD filter cannot be greater than maximum USD filter.")


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
    min_usd: float,
    max_usd: float,
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
        if min_usd <= value_usd <= max_usd
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
    min_usd: float = 10.0,
    max_usd: float = 100.0,
) -> Dict[str, Any]:
    """Fetch event data and calculate popular vote with Yes/No split."""
    event_slug = normalize_event_slug(event_slug_or_url)
    print(f"Fetching event: {event_slug}...", file=sys.stderr)
    event = get_event(event_slug)

    outcomes_data: List[Dict[str, Any]] = []
    all_voters: Set[str] = set()
    for market in event.get("markets", []):
        condition_id = market.get("conditionId")
        if not condition_id:
            continue
        outcome_name = market_name(market)

        outcome_prices = parse_json_list(market.get("outcomePrices"), ["0.5", "0.5"])
        yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
        no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5

        print(f"  Processing: {outcome_name}...", file=sys.stderr)

        positions = get_market_positions(str(condition_id))
        yes_holders = [position for position in positions if position.get("outcome") == "Yes"]
        no_holders = [position for position in positions if position.get("outcome") == "No"]

        yes_voters = filter_wallets(yes_holders, min_usd, max_usd, outcome_name, "Yes")
        no_voters = filter_wallets(no_holders, min_usd, max_usd, outcome_name, "No")

        combined_voters = yes_voters.union(no_voters)
        all_voters.update(combined_voters)

        outcomes_data.append(
            {
                "name": outcome_name,
                "voters": len(combined_voters),
                "yes_voters": len(yes_voters),
                "no_voters": len(no_voters),
                "yes_price": round(yes_price * 100, 1),
                "no_price": round(no_price * 100, 1),
            }
        )

    total_voters = len(all_voters)
    total_yes_votes = sum(outcome["yes_voters"] for outcome in outcomes_data)
    total_no_votes = sum(outcome["no_voters"] for outcome in outcomes_data)

    for outcome in outcomes_data:
        if total_yes_votes > 0:
            outcome["popular_pct"] = round(
                (outcome["yes_voters"] / total_yes_votes) * 100, 1
            )
        else:
            outcome["popular_pct"] = 0.0

        if total_no_votes > 0:
            outcome["unpopular_pct"] = round(
                (outcome["no_voters"] / total_no_votes) * 100, 1
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

    active_outcomes = [outcome for outcome in outcomes_data if outcome["voters"] > 0]
    active_outcomes.sort(key=lambda item: item["popular_pct"], reverse=True)

    return {
        "event_title": event.get("title", event_slug),
        "event_slug": event_slug,
        "filter_min_usd": min_usd,
        "filter_max_usd": max_usd,
        "total_voters": total_voters,
        "outcomes": active_outcomes,
        "timestamp": datetime.now().isoformat(),
    }


def render_cli_table(data: Dict[str, Any]) -> str:
    """Render the CLI table with Yes/No split."""
    lines = [
        "",
        f"EVENT: {data['event_title']}",
        (
            f"FILTER: Position size ${data['filter_min_usd']:,} - "
            f"${data['filter_max_usd']:,} USD | Total qualifying voters: "
            f"{data['total_voters']:,}"
        ),
        "",
        (
            f"{'RANK':<5} | {'OUTCOME':<22} | {'MKT YES':>7} | {'POP %':>7} | "
            f"{'VOTES':>7} | {'YES %':>6} | {'UNPOP %':>8} | {'VOTES':>7} | {'NO %':>6}"
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
        default=10.0,
        help="Minimum position value USD filter. Default: 10",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=100.0,
        help="Maximum position value USD filter. Default: 100",
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
        output_file = get_output_file_path(data["event_slug"], args.output)
        write_json(output_file, data)
        if args.print_table:
            print_cli_table(data)
        print(output_file)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

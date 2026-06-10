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
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import urlparse
import re
import urllib.parse

import requests

try:
    from web3 import Web3
except ImportError:
    Web3 = None  # type: ignore[assignment]

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
POLYGON_RPC_URLS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
    "https://1rpc.io/matic",
]
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
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


def _month_to_num(name: str) -> int | None:
    mapping = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    return mapping.get(name.lower())


def _parse_date(text: str) -> date | None:
    if not text:
        return None
    text = " " + re.sub(r"\s+", " ", text) + " "
    # e.g. 26 October 2025 or 26th October 2025
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        d, mon_name, y = m.groups()
        mon = _month_to_num(mon_name)
        if mon:
            try:
                return date(int(y), mon, int(d))
            except ValueError:
                pass
    # e.g. October 26, 2025
    m = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        mon_name, d, y = m.groups()
        mon = _month_to_num(mon_name)
        if mon:
            try:
                return date(int(y), mon, int(d))
            except ValueError:
                pass
    return None


def determine_prior_event_date(event: Dict[str, Any]) -> date | None:
    """Search the web (Wikipedia) for the actual event date; return the day *prior* for cutoff.
    Falls back to parsing endDate from event metadata if web search yields nothing.
    """
    title = str(event.get("title") or "")
    # Try Wikipedia first for real-world events (elections, etc.)
    try:
        search_q = title or "election date"
        opensearch_url = (
            "https://en.wikipedia.org/w/api.php?action=opensearch&search="
            + urllib.parse.quote(search_q)
            + "&limit=3&format=json&namespace=0"
        )
        sresp = requests.get(opensearch_url, headers=HEADERS, timeout=20)
        if sresp.ok:
            titles = sresp.json()[1] if isinstance(sresp.json(), list) else []
            for t in titles:
                extract_url = (
                    "https://en.wikipedia.org/w/api.php?action=query&format=json"
                    "&prop=extracts&exintro&explaintext&titles="
                    + urllib.parse.quote(t)
                    + "&redirects=1"
                )
                eresp = requests.get(extract_url, headers=HEADERS, timeout=20)
                if eresp.ok:
                    pages = eresp.json().get("query", {}).get("pages", {})
                    for page in pages.values():
                        extract = page.get("extract", "")
                        d = _parse_date(extract)
                        if d:
                            return d - timedelta(days=1)
    except Exception as exc:
        print(f"  Web date search failed: {exc}", file=sys.stderr)

    # Fallback: use market endDate minus one day (scheduled/expected event day)
    for market in event.get("markets", []):
        edstr = market.get("endDate") or market.get("endDateIso")
        if edstr:
            try:
                s = edstr
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                edt = datetime.fromisoformat(s)
                return edt.date() - timedelta(days=1)
            except Exception:
                continue
    return None


def is_event_finished(event: Dict[str, Any]) -> bool:
    """Detect resolved/finished markets by extreme pricing (one option ~100%, others ~0%)."""
    prices: List[float] = []
    for m in event.get("markets", []):
        ops = parse_json_list(m.get("outcomePrices"), ["0.5", "0.5"])
        for p in ops:
            try:
                prices.append(float(p))
            except (TypeError, ValueError):
                pass
    if prices:
        maxp = max(prices)
        minp = min(prices)
        if maxp >= 0.98 and minp <= 0.02:
            return True
    # also treat explicitly closed non-active
    if event.get("closed") and not event.get("active", True):
        return True
    return False


def _get_web3() -> "Web3 | None":
    if Web3 is None:
        return None
    for url in POLYGON_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 45}))
            if w3.is_connected():
                return w3
        except Exception:
            continue
    return None


def _get_block_by_timestamp(w3: "Web3", target_ts: int) -> int:
    """Estimate + binary search for highest block with ts <= target_ts. Tolerant of slow RPC."""
    try:
        latest = w3.eth.get_block("latest")
        high = latest.number
        latest_ts = latest.timestamp
    except Exception:
        high = 88_000_000
        latest_ts = int(__import__("time").time())
    # rough estimate: ~2.1s per block on Polygon
    delta_blocks = int((latest_ts - target_ts) / 2.1)
    low = max(1, high - max(0, delta_blocks + 50000))
    ans = low
    # narrow binary
    for _ in range(40):
        if low > high:
            break
        mid = (low + high) // 2
        try:
            blk = w3.eth.get_block(mid)
            if blk.timestamp <= target_ts:
                ans = mid
                low = mid + 1
            else:
                high = mid - 1
        except Exception:
            high = mid - 1
    # final walk up a bit if needed (cheap)
    for b in range(ans, min(ans + 2000, high + 1)):
        try:
            if w3.eth.get_block(b).timestamp > target_ts:
                break
            ans = b
        except Exception:
            break
    return ans


def _fetch_relevant_transfers(
    w3: "Web3", token_ints: List[int], start_block: int, end_block: int
) -> List[Dict[str, Any]]:
    """Chunked get_logs for TransferSingle involving known exchanges; filter to our token ids."""
    if not token_ints:
        return []
    ctf = w3.to_checksum_address(CTF_ADDRESS)
    ex_addrs = [
        w3.to_checksum_address(NEG_RISK_EXCHANGE_ADDRESS),
        w3.to_checksum_address(CTF_EXCHANGE_ADDRESS),
    ]
    abi = [
        {
            "anonymous": False,
            "inputs": [
                {"indexed": True, "name": "operator", "type": "address"},
                {"indexed": True, "name": "from", "type": "address"},
                {"indexed": True, "name": "to", "type": "address"},
                {"indexed": False, "name": "id", "type": "uint256"},
                {"indexed": False, "name": "value", "type": "uint256"},
            ],
            "name": "TransferSingle",
            "type": "event",
        }
    ]
    c = w3.eth.contract(address=ctf, abi=abi)
    sig = w3.keccak(text="TransferSingle(address,address,address,uint256,uint256)").hex()
    tid_set = set(token_ints)
    collected: List[Dict[str, Any]] = []
    seen = set()
    chunk = 10000
    for b0 in range(start_block, end_block + 1, chunk):
        b1 = min(b0 + chunk - 1, end_block)
        for ex in ex_addrs:
            ex_p = "0x" + ex[2:].lower().rjust(64, "0")
            for from_idx in [1, 2]:
                topics = [sig] + [None] * 3
                topics[from_idx] = ex_p
                try:
                    lg = w3.eth.get_logs({
                        "address": ctf,
                        "fromBlock": b0,
                        "toBlock": b1,
                        "topics": topics,
                    })
                    for log in lg:
                        key = (log.blockNumber, getattr(log, "logIndex", 0))
                        if key in seen:
                            continue
                        seen.add(key)
                        try:
                            ev = c.events.TransferSingle().process_log(log)
                            tid = int(ev.args.id)
                            if tid in tid_set:
                                collected.append({
                                    "block": log.blockNumber,
                                    "logIndex": getattr(log, "logIndex", 0),
                                    "from": ev.args["from"],
                                    "to": ev.args.to,
                                    "id": tid,
                                    "value": int(ev.args.value),
                                })
                        except Exception:
                            continue
                except Exception:
                    pass
    collected.sort(key=lambda x: (x["block"], x["logIndex"]))
    return collected


def get_historical_market_positions_and_price(
    condition_id: str, cutoff_ts: int, clob_token_ids: List[str] | None = None
) -> tuple[List[Dict[str, Any]], float, float]:
    """Build positions from on-chain CTF transfers (preferred, complete history) with trade data for price derivation.
    Falls back to trade replay for positions when on-chain not available.
    """
    token_ints: List[int] = []
    if clob_token_ids:
        for t in clob_token_ids:
            try:
                token_ints.append(int(str(t)))
            except Exception:
                pass

    # Pull trade records pre-cutoff (for price mainly; positions secondary)
    all_trades: List[Dict[str, Any]] = []
    offset = 0
    page_size = 10000
    url = f"{DATA_API}/trades"
    while True:
        params = {
            "market": condition_id,
            "limit": page_size,
            "offset": offset,
            "takerOnly": "false",
        }
        resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        all_trades.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        if offset > 500000:
            break
        time.sleep(0.03)

    cutoff_trades = [t for t in all_trades if int(t.get("timestamp") or 0) <= cutoff_ts]
    cutoff_trades.sort(key=lambda t: (int(t.get("timestamp") or 0), str(t.get("transactionHash") or "")))

    yes_p = 0.5
    no_p = 0.5
    if cutoff_trades:
        latest = max(cutoff_trades, key=lambda t: int(t.get("timestamp") or 0))
        try:
            lp = float(latest.get("price") or 0.5)
            lout = latest.get("outcome")
            if lout == "Yes":
                yes_p = lp
                no_p = max(0.0, min(1.0, 1.0 - lp))
            elif lout == "No":
                no_p = lp
                yes_p = max(0.0, min(1.0, 1.0 - lp))
        except (TypeError, ValueError):
            pass

    positions: List[Dict[str, Any]] = []

    # Primary: on-chain via direct CTF logs (works even when /trades is retention-limited)
    w3 = _get_web3()
    used_onchain = False
    do_onchain = bool(w3 and token_ints and cutoff_ts and len(cutoff_trades) < 50)
    if do_onchain:
        try:
            start_ts = cutoff_ts - int(290 * 86400)
            start_b = _get_block_by_timestamp(w3, start_ts)
            end_b = _get_block_by_timestamp(w3, cutoff_ts)
            if end_b - start_b > 8_000_000:
                print(f"  On-chain range too large ({start_b}-{end_b}); skipping for speed, using trade fallback.", file=sys.stderr)
                do_onchain = False
            else:
                print(
                    f"  On-chain snapshot (trades API sparse): blocks ~{datetime.fromtimestamp(start_ts, tz=timezone.utc).date()}..{datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).date()}",
                    file=sys.stderr,
                )
                print(f"  Fetching CTF transfers ({start_b}-{end_b})...", file=sys.stderr)
                xfers = _fetch_relevant_transfers(w3, token_ints, start_b, end_b)
                print(f"    {len(xfers)} transfers matched for market.", file=sys.stderr)

                DECIMALS = 1_000_000
                bals: Dict[tuple[str, int], float] = defaultdict(float)
                ex_set = {a.lower() for a in (NEG_RISK_EXCHANGE_ADDRESS, CTF_EXCHANGE_ADDRESS)}
                for xf in xfers:
                    frm = str(xf["from"]).lower()
                    to = str(xf["to"]).lower()
                    tid = xf["id"]
                    val = xf["value"] / DECIMALS
                    if to in ex_set or to == "0x" + "0" * 40:
                        bals[(frm, tid)] -= val
                    else:
                        bals[(to, tid)] += val

                yes_tid = token_ints[0] if token_ints else None
                no_tid = token_ints[1] if len(token_ints) > 1 else None
                holdings: Dict[str, Dict[str, float]] = defaultdict(lambda: {"Yes": 0.0, "No": 0.0})
                for (wal, tid), v in bals.items():
                    if v <= 0.00005:
                        continue
                    if tid == yes_tid:
                        holdings[wal]["Yes"] += v
                    elif tid == no_tid:
                        holdings[wal]["No"] += v

                for wal, h in holdings.items():
                    for outc, sz in h.items():
                        if sz > 0.0001:
                            p = yes_p if outc == "Yes" else no_p
                            positions.append({"proxyWallet": wal, "outcome": outc, "currentValue": sz * p})
                used_onchain = True
        except Exception as exc:
            print(f"  On-chain positions failed: {exc}; using trade replay fallback.", file=sys.stderr)

    if not used_onchain:
        holdings: Dict[str, Dict[str, float]] = defaultdict(lambda: {"Yes": 0.0, "No": 0.0})
        for t in cutoff_trades:
            wallet = t.get("proxyWallet")
            if not wallet:
                continue
            outc = t.get("outcome")
            if outc not in ("Yes", "No"):
                continue
            side = str(t.get("side") or "").upper()
            try:
                sz = float(t.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if side == "BUY":
                holdings[wallet][outc] += sz
            elif side == "SELL":
                holdings[wallet][outc] -= sz
        for wallet, h in holdings.items():
            for outc, sz in h.items():
                if sz > 0.0001:
                    p = yes_p if outc == "Yes" else no_p
                    val = sz * p
                    positions.append({"proxyWallet": wallet, "outcome": outc, "currentValue": val})

    return positions, round(yes_p, 4), round(no_p, 4)


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
    """Fetch event data and calculate popular vote with Yes/No split.
    For finished/resolved events (detected via extreme current prices), determines
    the day prior via web search and replays on-chain trade history (via data API)
    up to that cutoff to snapshot pre-event positions.
    """
    event_slug = normalize_event_slug(event_slug_or_url)
    print(f"Fetching event: {event_slug}...", file=sys.stderr)
    event = get_event(event_slug)

    cutoff_date: date | None = None
    cutoff_ts: int | None = None
    if is_event_finished(event):
        print(
            "Detected finished event (one option ~100%, others ~0%). Searching web for event date to snapshot pre-event positions...",
            file=sys.stderr,
        )
        cutoff_date = determine_prior_event_date(event)
        if cutoff_date:
            cutoff_dt = datetime.combine(
                cutoff_date, datetime.max.time(), tzinfo=timezone.utc
            )
            cutoff_ts = int(cutoff_dt.timestamp())
            print(f"  Using cutoff: {cutoff_date} (end of day prior to event)", file=sys.stderr)
        else:
            print(
                "  Could not determine prior cutoff date; falling back to current on-chain positions.",
                file=sys.stderr,
            )

    outcomes_data: List[Dict[str, Any]] = []
    all_voters: Set[str] = set()
    for market in event.get("markets", []):
        condition_id = market.get("conditionId")
        if not condition_id:
            continue
        outcome_name = market_name(market)

        print(f"  Processing: {outcome_name}...", file=sys.stderr)

        if cutoff_ts is not None:
            tids = parse_json_list(market.get("clobTokenIds"), [])
            positions, yes_price, no_price = get_historical_market_positions_and_price(
                str(condition_id), cutoff_ts, tids
            )
        else:
            outcome_prices = parse_json_list(market.get("outcomePrices"), ["0.5", "0.5"])
            yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5
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
        "cutoff": cutoff_date.isoformat() if cutoff_date else None,
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
    ]
    if data.get("cutoff"):
        lines.append(f"CUTOFF: pre-event positions as of end of {data['cutoff']} (historical on-chain snapshot)")
    lines.extend(
        [
            "",
            (
                f"{'RANK':<5} | {'OUTCOME':<22} | {'MKT YES':>7} | {'POP':>7} | "
                f"{'VOTES':>7} | {'YES':>6} | {'UNPOP':>8} | {'VOTES':>7} | {'NO':>6}"
            ),
            "-" * 101,
        ]
    )

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

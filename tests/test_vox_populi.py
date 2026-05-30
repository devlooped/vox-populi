import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vox_populi  # noqa: E402


class VoxPopuliTests(unittest.TestCase):
    def test_filter_wallets_uses_largest_position_per_wallet(self) -> None:
        positions = [
            {"proxyWallet": "0xa", "currentValue": 50},
            {"proxyWallet": "0xa", "currentValue": 150},
            {"proxyWallet": "0xb", "currentValue": 5},
            {"proxyWallet": "0xb", "currentValue": 20},
            {"proxyWallet": "", "currentValue": 20},
        ]

        wallets = vox_populi.filter_wallets(
            positions=positions,
            min_usd=10,
            max_usd=100,
            outcome_name="Candidate",
            side="Yes",
        )

        self.assertEqual(wallets, {"0xb"})

    @patch("vox_populi.get_market_positions")
    @patch("vox_populi.get_event")
    def test_fetch_vox_populi_adds_unpopular_pct_and_dedupes_by_largest_position(
        self, mock_get_event, mock_get_market_positions
    ) -> None:
        mock_get_event.return_value = {
            "title": "Test Event",
            "markets": [
                {
                    "conditionId": "1",
                    "groupItemTitle": "Outcome A",
                    "outcomePrices": ["0.6", "0.4"],
                }
            ],
        }
        mock_get_market_positions.return_value = [
            {"outcome": "Yes", "proxyWallet": "0x1", "currentValue": 20},
            {"outcome": "Yes", "proxyWallet": "0x1", "currentValue": 200},
            {"outcome": "Yes", "proxyWallet": "0x2", "currentValue": 30},
            {"outcome": "No", "proxyWallet": "0x3", "currentValue": 5},
            {"outcome": "No", "proxyWallet": "0x3", "currentValue": 40},
            {"outcome": "No", "proxyWallet": "0x2", "currentValue": 12},
        ]

        result = vox_populi.fetch_vox_populi("test-event", min_usd=10, max_usd=100)

        self.assertEqual(result["total_voters"], 2)
        self.assertEqual(len(result["outcomes"]), 1)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["yes_voters"], 1)
        self.assertEqual(outcome["no_voters"], 2)
        self.assertEqual(outcome["voters"], 2)
        self.assertEqual(outcome["unpopular_pct"], 100.0)

    def test_render_table_contains_unpopular_column(self) -> None:
        data = {
            "event_title": "Event",
            "filter_min_usd": 10.0,
            "filter_max_usd": 100.0,
            "total_voters": 2,
            "timestamp": "2026-05-30T00:00:00",
            "outcomes": [
                {
                    "name": "Outcome A",
                    "popular_pct": 100.0,
                    "voters": 2,
                    "yes_pct": 50.0,
                    "unpopular_pct": 100.0,
                    "no_pct": 100.0,
                    "yes_price": 60.0,
                }
            ],
        }

        table = vox_populi.render_cli_table(data)

        self.assertIn("UNPOP %", table)
        self.assertIn("100.0%", table)


if __name__ == "__main__":
    unittest.main()

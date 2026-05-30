import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vox_populi  # noqa: E402


class VoxPopuliTests(unittest.TestCase):
    def test_parser_defaults_to_unbounded_bounds(self) -> None:
        args = vox_populi.build_parser().parse_args(["test-event"])
        self.assertIsNone(args.min_usd)
        self.assertIsNone(args.max_usd)

    def test_filter_wallets_allows_one_sided_or_unbounded_ranges(self) -> None:
        positions = [
            {"proxyWallet": "0xa", "currentValue": 5},
            {"proxyWallet": "0xb", "currentValue": 25},
            {"proxyWallet": "0xc", "currentValue": 500},
        ]

        unbounded = vox_populi.filter_wallets(
            positions=positions,
            min_usd=None,
            max_usd=None,
            outcome_name="Candidate",
            side="Yes",
        )
        at_least_25 = vox_populi.filter_wallets(
            positions=positions,
            min_usd=25,
            max_usd=None,
            outcome_name="Candidate",
            side="Yes",
        )
        up_to_25 = vox_populi.filter_wallets(
            positions=positions,
            min_usd=None,
            max_usd=25,
            outcome_name="Candidate",
            side="Yes",
        )

        self.assertEqual(unbounded, {"0xa", "0xb", "0xc"})
        self.assertEqual(at_least_25, {"0xb", "0xc"})
        self.assertEqual(up_to_25, {"0xa", "0xb"})

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

    @patch("vox_populi.get_market_positions")
    @patch("vox_populi.get_event")
    def test_fetch_vox_populi_defaults_to_unbounded_filter(
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

        result = vox_populi.fetch_vox_populi("test-event")

        self.assertIsNone(result["filter_min_usd"])
        self.assertIsNone(result["filter_max_usd"])
        self.assertEqual(result["total_voters"], 3)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome["yes_voters"], 2)
        self.assertEqual(outcome["no_voters"], 2)

    @patch("vox_populi.get_market_positions")
    @patch("vox_populi.get_event")
    def test_fetch_vox_populi_popular_and_unpopular_sum_to_100(
        self, mock_get_event, mock_get_market_positions
    ) -> None:
        mock_get_event.return_value = {
            "title": "Test Event",
            "markets": [
                {
                    "conditionId": "1",
                    "groupItemTitle": "Outcome A",
                    "outcomePrices": ["0.6", "0.4"],
                },
                {
                    "conditionId": "2",
                    "groupItemTitle": "Outcome B",
                    "outcomePrices": ["0.3", "0.7"],
                },
            ],
        }
        mock_get_market_positions.side_effect = [
            [
                {"outcome": "Yes", "proxyWallet": "0x1", "currentValue": 20},
                {"outcome": "Yes", "proxyWallet": "0x2", "currentValue": 30},
                {"outcome": "No", "proxyWallet": "0x3", "currentValue": 40},
            ],
            [
                {"outcome": "Yes", "proxyWallet": "0x4", "currentValue": 25},
                {"outcome": "No", "proxyWallet": "0x5", "currentValue": 30},
                {"outcome": "No", "proxyWallet": "0x6", "currentValue": 35},
            ],
        ]

        result = vox_populi.fetch_vox_populi("test-event", min_usd=10, max_usd=100)

        popular_total = round(sum(outcome["popular_pct"] for outcome in result["outcomes"]), 1)
        unpopular_total = round(
            sum(outcome["unpopular_pct"] for outcome in result["outcomes"]), 1
        )

        self.assertEqual(popular_total, 100.0)
        self.assertEqual(unpopular_total, 100.0)

    @patch("vox_populi.get_market_positions")
    @patch("vox_populi.get_event")
    def test_fetch_vox_populi_excludes_outcomes_below_one_pop_pct(
        self, mock_get_event, mock_get_market_positions
    ) -> None:
        mock_get_event.return_value = {
            "title": "Test Event",
            "markets": [
                {
                    "conditionId": "1",
                    "groupItemTitle": "Outcome A",
                    "outcomePrices": ["0.6", "0.4"],
                },
                {
                    "conditionId": "2",
                    "groupItemTitle": "Outcome B",
                    "outcomePrices": ["0.3", "0.7"],
                },
            ],
        }
        mock_get_market_positions.side_effect = [
            [{"outcome": "Yes", "proxyWallet": "0xyes-a", "currentValue": 20}]
            + [
                {
                    "outcome": "No",
                    "proxyWallet": f"0xno-a-{wallet}",
                    "currentValue": 20,
                }
                for wallet in range(200)
            ],
            [{"outcome": "No", "proxyWallet": "0xno-b", "currentValue": 20}]
            + [
                {
                    "outcome": "Yes",
                    "proxyWallet": f"0xyes-b-{wallet}",
                    "currentValue": 20,
                }
                for wallet in range(199)
            ],
        ]

        result = vox_populi.fetch_vox_populi("test-event", min_usd=10, max_usd=100)

        self.assertEqual(len(result["outcomes"]), 1)
        self.assertEqual(result["outcomes"][0]["name"], "Outcome B")

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
                    "yes_voters": 2,
                    "no_voters": 0,
                    "yes_pct": 100.0,
                    "unpopular_pct": 0.0,
                    "no_pct": 0.0,
                    "yes_price": 60.0,
                }
            ],
        }

        table = vox_populi.render_cli_table(data)

        self.assertIn("UNPOP", table)
        self.assertIn("MKT YES", table)
        self.assertLess(table.find("MKT YES"), table.find("POP"))
        self.assertNotIn("YES %", table)
        self.assertNotIn("NO %", table)
        self.assertIn("100.0%", table)


if __name__ == "__main__":
    unittest.main()

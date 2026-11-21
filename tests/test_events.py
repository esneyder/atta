import json
import unittest
import warnings

import support  # noqa: F401
from support import TempDirMixin, T0

from spine import events


PRICES = {"claude-sonnet": {"input": 3.0, "output": 15.0}}


class CostTests(unittest.TestCase):
    def test_cost_with_price(self):
        usage = {"tokens_in": 1_000_000, "tokens_out": 200_000}
        self.assertEqual(events.cost("claude-sonnet", usage, PRICES), 6.0)

    def test_missing_price_returns_none_and_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = events.cost("modelo-raro", {"tokens_in": 10, "tokens_out": 10}, PRICES)
        self.assertIsNone(result)
        self.assertEqual(len(caught), 1)
        self.assertTrue(issubclass(caught[0].category, events.MissingPriceWarning))
        self.assertEqual(
            str(caught[0].message),
            "Sin precio para el modelo «modelo-raro» en prices_usd_per_mtok; costo = null.",
        )

    def test_missing_price_with_empty_table(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            self.assertIsNone(events.cost("claude-sonnet", {"tokens_in": 1}, {}))


class AppendTests(TempDirMixin, unittest.TestCase):
    def test_one_json_line_per_event(self):
        events.append_event(self.tmp, events.make_event("1-a", "intake", "spec", 2.5, now=T0))
        events.append_event(self.tmp, events.make_event(
            "1-a", "spec", "build", 10, now=T0, model="claude-sonnet", tier="mid",
            tokens_in=100, tokens_out=50, cost_usd=None))
        raw = (self.tmp / "events.jsonl").read_bytes()
        self.assertNotIn(b"\r\n", raw)
        lines = raw.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {
            "ts": "2025-01-01T12:00:00Z", "job": "1-a", "from": "intake", "to": "spec",
            "duration_s": 2.5,
        })
        self.assertEqual(json.loads(lines[1]), {
            "ts": "2025-01-01T12:00:00Z", "job": "1-a", "from": "spec", "to": "build",
            "duration_s": 10, "model": "claude-sonnet", "tier": "mid",
            "tokens_in": 100, "tokens_out": 50, "cost_usd": None,
        })

    def test_read_events(self):
        events.append_event(self.tmp, events.make_event("1-a", "intake", "spec", 1, now=T0))
        self.assertEqual([e["to"] for e in events.read_events(self.tmp)], ["spec"])
        self.assertEqual(events.read_events(self.tmp / "nope"), [])


if __name__ == "__main__":
    unittest.main()

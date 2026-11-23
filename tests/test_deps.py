"""BLOCKED-BY entre jobs (F4 S7, K5): `atta.spine.deps` decide quién espera y quién se detiene."""

import unittest

import support  # noqa: F401

from atta.spine import deps


def job(issue, stage="build", blocked_by=None, started="2026-01-01T00:00:00Z"):
    j = {"id": f"{issue}-tarea", "issue": issue, "stage": stage, "started": started}
    if blocked_by is not None:
        j["intake"] = {"risk": "low", "surfaces": [], "decision": "ACCEPT",
                       "blocked_by": list(blocked_by), "migration": False}
    return j


class ByIssueTests(unittest.TestCase):
    def test_active_job_wins_over_a_newer_terminal_one(self):
        old = job(5, "build", started="2026-01-01T00:00:00Z")
        newer = job(5, "rejected", started="2026-02-01T00:00:00Z")
        self.assertIs(deps.by_issue([old, newer])[5], old)
        self.assertIs(deps.job_for_issue([old, newer], 5), old)
        self.assertIsNone(deps.job_for_issue([old], 6))

    def test_most_recent_terminal_job(self):
        a = job(5, "rejected", started="2026-01-01T00:00:00Z")
        b = job(5, "merged", started="2026-02-01T00:00:00Z")
        self.assertIs(deps.by_issue([b, a])[5], b)


class CheckTests(unittest.TestCase):
    def check(self, target, others, closed=None):
        return deps.check(target, deps.by_issue([target, *others]), closed)

    def test_without_blocked_by_nothing_changes(self):
        self.assertIsNone(self.check(job(5), []))
        self.assertIsNone(self.check(job(5, blocked_by=[]), []))
        self.assertEqual(deps.blocked_by(job(5)), [])

    def test_unmerged_blocker_waits(self):
        self.assertEqual(self.check(job(5, blocked_by=[7]), [job(7, "build")]),
                         {"action": "wait", "blockers": [7],
                          "reason": "espera a #7: todavía no está mergeado."})

    def test_blocker_without_job_and_open_issue_waits(self):
        self.assertEqual(self.check(job(5, blocked_by=[7, 9]), [])["reason"],
                         "espera a #7 y #9: todavía no están mergeados.")
        self.assertEqual(self.check(job(5, blocked_by=[7, 8, 9]), [])["reason"],
                         "espera a #7, #8 y #9: todavía no están mergeados.")

    def test_merged_job_or_closed_issue_resolves(self):
        self.assertIsNone(self.check(job(5, blocked_by=[7]), [job(7, "merged")]))
        self.assertIsNone(self.check(job(5, blocked_by=[7]), [], closed=lambda n: n == 7))
        self.assertEqual(deps.unresolved(job(5, blocked_by=[7, 9]),
                                         deps.by_issue([job(7, "merged")]),
                                         closed=lambda n: False), [9])

    def test_rejected_blocker_holds(self):
        target = job(5, blocked_by=[7, 9])
        others = [job(7, "rejected"), job(9, "build")]
        self.assertEqual(deps.rejected(target, deps.by_issue(others)), [7])
        self.assertEqual(self.check(target, others),
                         {"action": "hold", "blockers": [7],
                          "reason": "su bloqueante #7 fue rechazado: decide una persona."})
        both = [job(7, "rejected"), job(9, "rejected")]
        self.assertEqual(self.check(target, both)["reason"],
                         "sus bloqueantes #7 y #9 fueron rechazados: decide una persona.")

    def test_rejected_wins_over_a_closed_issue(self):
        # Rejecting closes the issue; the dependent still must not advance.
        verdict = self.check(job(5, blocked_by=[7]), [job(7, "rejected")], closed=lambda n: True)
        self.assertEqual(verdict["action"], "hold")

    def test_cycle_holds(self):
        a, b = job(5, blocked_by=[7]), job(7, blocked_by=[5])
        self.assertEqual(deps.cycle(a, deps.by_issue([a, b])), [5, 7, 5])
        self.assertEqual(self.check(a, [b]),
                         {"action": "hold", "blockers": [5, 7, 5],
                          "reason": "dependencia circular (#5 → #7 → #5): decide una persona."})
        self.assertEqual(self.check(job(5, blocked_by=[5]), [])["reason"],
                         "dependencia circular (#5 → #5): decide una persona.")

    def test_longer_cycle_and_cycle_elsewhere(self):
        a, b, c = job(5, blocked_by=[7]), job(7, blocked_by=[9]), job(9, blocked_by=[5])
        self.assertEqual(deps.cycle(a, deps.by_issue([a, b, c])), [5, 7, 9, 5])
        # 5 waits on 7, which is stuck in a 7 ↔ 9 cycle: 5 only waits; 7 and 9 hold.
        a, b, c = job(5, blocked_by=[7]), job(7, blocked_by=[9]), job(9, blocked_by=[7])
        self.assertEqual(self.check(a, [b, c])["action"], "wait")
        self.assertEqual(self.check(b, [a, c])["action"], "hold")

    def test_merged_link_breaks_the_cycle(self):
        a, b = job(5, blocked_by=[7]), job(7, "merged", blocked_by=[5])
        self.assertIsNone(self.check(a, [b]))

    def test_malformed_intake_is_ignored(self):
        broken = {"id": "5-x", "issue": 5, "stage": "build", "intake": {"blocked_by": "#7"}}
        self.assertEqual(deps.blocked_by(broken), [])
        self.assertEqual(deps.blocked_by({"id": "5-x", "intake": None}), [])


if __name__ == "__main__":
    unittest.main()

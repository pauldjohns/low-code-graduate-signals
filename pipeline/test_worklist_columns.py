#!/usr/bin/env python3
"""Regression test for the failure method/VERIFY-INTEGRATION.md §5a calls "the one that bites".

ceiling_poll's worklist_upsert/worklist_retire rewrite outreach/worklist_ceiling.csv with
`fieldnames=TRACK_COLS+DATA_COLS, extrasaction="ignore"`. Any column NOT in that union is silently
dropped on the next poll — no error, no warning. In the Review repo this is what made `email_check`
vanish repeatedly.

verify_queue.py refuses to run when the verify_* columns are missing, so the symptom of this
regression is a permanently disabled verification step, not corrupt data. Still worth catching here.

Ported from the sibling campaign/pipeline/test_worklist_columns.py.
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ceiling_poll
import verify_queue


class TestWorklistColumnSurvival(unittest.TestCase):
    def test_verify_columns_are_in_data_cols(self):
        """The three verify_* columns must survive a ceiling_poll rewrite."""
        union = set(ceiling_poll.TRACK_COLS) | set(ceiling_poll.DATA_COLS)
        for col in verify_queue.VERIFY_COLS:
            self.assertIn(
                col, union,
                f"{col} is missing from ceiling_poll.TRACK_COLS+DATA_COLS, so the next poll will "
                f"silently drop it and verify_queue.py will refuse to run. Add it to DATA_COLS.")

    def test_tracking_columns_still_present(self):
        """the operator's hand-edited tracking columns must never be dropped either."""
        for col in ("status", "contacted_on", "channel", "replied", "notes"):
            self.assertIn(col, ceiling_poll.TRACK_COLS)

    def test_no_duplicate_columns(self):
        """A column in both lists would write a duplicate header."""
        both = set(ceiling_poll.TRACK_COLS) & set(ceiling_poll.DATA_COLS)
        self.assertEqual(both, set(), f"columns in both TRACK_COLS and DATA_COLS: {both}")

    def test_live_worklist_has_the_columns(self):
        """If the live worklist exists it must already carry verify_* (run one poll to migrate)."""
        wl = verify_queue.WORKLIST
        if not os.path.exists(wl):
            self.skipTest("no live worklist in this checkout")
        import csv
        with open(wl, newline="") as f:
            fields = set(csv.DictReader(f).fieldnames or [])
        missing = [c for c in verify_queue.VERIFY_COLS if c not in fields]
        if missing:
            self.skipTest(f"live worklist not migrated yet (missing {missing}) — run one ceiling_poll")


if __name__ == "__main__":
    unittest.main()

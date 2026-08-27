#!/usr/bin/env python3
"""Tests for the Bouncer verification step. No network: v_bouncer is stubbed throughout.

Covers the rules method/VERIFY-INTEGRATION.md §4 says matter more than the API call:
only `undeliverable` is terminal, a vendor error is not a verdict, the cull ceiling, and
idempotency / never touching hand-edited tracking columns.
"""
import csv, os, sys, tempfile, unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_queue as vq

FIELDS = ["status", "contacted_on", "channel", "replied", "notes",
          "segment", "email", "owner_repo", "verify_status", "verify_reason", "verify_date"]


def row(**kw):
    r = {k: "" for k in FIELDS}
    r.update(kw)
    return r


def write_worklist(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)


def read_worklist(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class StubVendor:
    """Stands in for verify_bouncer. Returns queued (verdict, raw, detail) triples in order."""
    def __init__(self, replies):
        self.replies = list(replies); self.calls = []
        self.ENV = "/nonexistent/verify.env"
        self.ENV_CANDIDATES = ["/nonexistent/verify.env"]

    def v_bouncer(self, email, key):
        self.calls.append(email)
        return self.replies.pop(0) if self.replies else ("unsure", "unknown", "")

    def load_key(self, name):
        return "stub-key"


class TestKeyLookup(unittest.TestCase):
    """The key lives in the SHARED secrets dir; an engine-local file may override it later."""

    def setUp(self):
        import verify_bouncer
        self.vb = verify_bouncer
        self._cands = list(verify_bouncer.ENV_CANDIDATES)
        self._env = os.environ.pop("BOUNCER_API_KEY", None)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.vb.ENV_CANDIDATES = self._cands
        if self._env is not None:
            os.environ["BOUNCER_API_KEY"] = self._env
        else:
            os.environ.pop("BOUNCER_API_KEY", None)

    def _write(self, name, body):
        p = os.path.join(self.tmp, name)
        open(p, "w").write(body)
        return p

    def test_env_var_wins_over_every_file(self):
        f = self._write("a.env", "BOUNCER_API_KEY=from_file\n")
        self.vb.ENV_CANDIDATES = [f]
        os.environ["BOUNCER_API_KEY"] = "from_env"
        self.assertEqual(self.vb.load_key("BOUNCER_API_KEY"), "from_env")

    def test_falls_through_to_the_shared_file(self):
        missing = os.path.join(self.tmp, "nope.env")
        shared = self._write("shared.env", "BOUNCER_API_KEY=shared_key\nZEROBOUNCE_API_KEY=z\n")
        self.vb.ENV_CANDIDATES = [missing, shared]
        self.assertEqual(self.vb.load_key("BOUNCER_API_KEY"), "shared_key")

    def test_engine_local_file_overrides_the_shared_one(self):
        local = self._write("local.env", "BOUNCER_API_KEY=local_key\n")
        shared = self._write("shared.env", "BOUNCER_API_KEY=shared_key\n")
        self.vb.ENV_CANDIDATES = [local, shared]
        self.assertEqual(self.vb.load_key("BOUNCER_API_KEY"), "local_key")

    def test_absent_everywhere_is_empty_not_an_exception(self):
        self.vb.ENV_CANDIDATES = [os.path.join(self.tmp, "nope.env")]
        self.assertEqual(self.vb.load_key("BOUNCER_API_KEY"), "")

    def test_picks_the_right_key_out_of_a_multi_key_file(self):
        f = self._write("m.env", "ZEROBOUNCE_API_KEY=zb\nBOUNCER_API_KEY=bk\n")
        self.vb.ENV_CANDIDATES = [f]
        self.assertEqual(self.vb.load_key("BOUNCER_API_KEY"), "bk")
        self.assertEqual(self.vb.load_key("ZEROBOUNCE_API_KEY"), "zb")

    def test_shared_dir_is_the_configured_fallback(self):
        """Guards against someone re-splitting the key and quietly halving the credit pool."""
        self.assertTrue(self._cands[-1].endswith("shared-outreach/verify.env"), self._cands)


class TestClassify(unittest.TestCase):
    def test_deliverable_splits_on_accept_all(self):
        self.assertEqual(vq.classify("deliverable", False), "deliverable")
        self.assertEqual(vq.classify("deliverable", True), "deliverable_acceptall")

    def test_known_statuses_pass_through(self):
        for st in ("undeliverable", "risky", "unknown"):
            self.assertEqual(vq.classify(st, False), st)

    def test_unknown_vendor_status_records_itself_never_culls(self):
        self.assertEqual(vq.classify("some_new_status", False), "some_new_status")
        self.assertNotEqual(vq.classify("some_new_status", False), vq.TERMINAL)

    def test_empty_status_becomes_unknown(self):
        self.assertEqual(vq.classify("", False), "unknown")
        self.assertEqual(vq.classify(None, False), "unknown")

    def test_case_and_whitespace(self):
        self.assertEqual(vq.classify("  UNDELIVERABLE ", False), "undeliverable")


class TestParseDetail(unittest.TestCase):
    def test_pulls_accept_all_and_reason(self):
        aa, reason = vq._parse_detail("rejected_email acceptAll=yes provider=google.com")
        self.assertTrue(aa); self.assertEqual(reason, "rejected_email")

    def test_accept_all_no_is_false(self):
        aa, reason = vq._parse_detail("accepted_email acceptAll=no provider=google.com")
        self.assertFalse(aa); self.assertEqual(reason, "accepted_email")

    def test_empty(self):
        self.assertEqual(vq._parse_detail(""), (False, ""))
        self.assertEqual(vq._parse_detail(None), (False, ""))


class TestNeedsVerify(unittest.TestCase):
    def test_blank_status_never_seen_is_eligible(self):
        self.assertTrue(vq._needs_verify(row(email="a@b.com")))

    def test_hand_edited_status_is_never_touched(self):
        for st in ("sent", "skipped", "replied", "graduated", "bounced"):
            self.assertFalse(vq._needs_verify(row(email="a@b.com", status=st)))

    def test_no_email_is_ineligible(self):
        self.assertFalse(vq._needs_verify(row(email="")))

    def test_settled_verdict_with_reason_is_not_rebilled(self):
        for st in ("deliverable", "undeliverable", "risky", "deliverable_acceptall"):
            self.assertFalse(vq._needs_verify(
                row(email="a@b.com", verify_status=st, verify_reason="r")))

    def test_unknown_is_reverified_after_the_cooldown(self):
        old = (date.today() - timedelta(days=vq.UNKNOWN_RECHECK_DAYS)).isoformat()
        self.assertTrue(vq._needs_verify(
            row(email="a@b.com", verify_status="unknown", verify_reason="timeout", verify_date=old)))

    def test_unknown_is_not_rebilled_inside_the_cooldown(self):
        """Without this, a chronically-unknown address is re-billed every hourly cycle forever."""
        fresh = date.today().isoformat()
        self.assertFalse(vq._needs_verify(
            row(email="a@b.com", verify_status="unknown", verify_reason="timeout", verify_date=fresh)))

    def test_unknown_with_no_date_is_due(self):
        self.assertTrue(vq._needs_verify(
            row(email="a@b.com", verify_status="unknown", verify_reason="timeout")))
        self.assertTrue(vq._needs_verify(
            row(email="a@b.com", verify_status="unknown", verify_reason="t", verify_date="garbage")))

    def test_verdict_without_reason_is_backfilled_once(self):
        self.assertTrue(vq._needs_verify(row(email="a@b.com", verify_status="deliverable")))


class TestVerifyRows(unittest.TestCase):
    def setUp(self):
        self._real = vq.vb

    def tearDown(self):
        vq.vb = self._real

    def test_vendor_error_stops_the_run_and_is_not_a_verdict(self):
        vq.vb = StubVendor([("good", "deliverable", "ok acceptAll=no"),
                            ("error", "HTTP402", ""),
                            ("good", "deliverable", "ok acceptAll=no")])
        rows = [row(email="a@x.com"), row(email="b@x.com"), row(email="c@x.com")]
        results, errors = vq.verify_rows(rows, "k", 0, log=lambda *a: None)
        self.assertEqual(errors, 1)
        self.assertEqual(set(results), {"a@x.com"})     # b errored, c never called
        self.assertEqual(len(vq.vb.calls), 2)

    def test_one_email_across_repos_is_billed_once(self):
        vq.vb = StubVendor([("good", "deliverable", "ok acceptAll=no")])
        rows = [row(email="dup@x.com", owner_repo="o/r1"), row(email="dup@x.com", owner_repo="o/r2")]
        results, errors = vq.verify_rows(rows, "k", 0, log=lambda *a: None)
        self.assertEqual(vq.vb.calls, ["dup@x.com"])
        self.assertEqual(len(results), 1)


class TestSweep(unittest.TestCase):
    def setUp(self):
        self._real = vq.vb
        self.tmp = tempfile.mkdtemp()
        self.wl = os.path.join(self.tmp, "worklist_ceiling.csv")

    def tearDown(self):
        vq.vb = self._real

    def test_dry_run_makes_no_calls_and_writes_nothing(self):
        vq.vb = StubVendor([])
        write_worklist(self.wl, [row(email="a@x.com")])
        before = open(self.wl).read()
        rc = vq.sweep(apply=False, limit=0, worklist=self.wl, key="k", lock_path=None)
        self.assertEqual(rc, 0)
        self.assertEqual(vq.vb.calls, [])
        self.assertEqual(open(self.wl).read(), before)

    def test_missing_verify_columns_refuses_to_run(self):
        path = os.path.join(self.tmp, "old.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["status", "email", "notes"])
            w.writeheader(); w.writerow({"status": "", "email": "a@x.com", "notes": ""})
        self.assertEqual(vq.sweep(apply=True, limit=0, worklist=path, key="k", lock_path=None), 1)

    def test_only_undeliverable_is_terminal(self):
        vq.vb = StubVendor([("bad", "undeliverable", "rejected acceptAll=no"),
                            ("unsure", "risky", "low_deliverability acceptAll=no"),
                            ("unsure", "unknown", "timeout acceptAll=no"),
                            ("good", "deliverable", "accepted acceptAll=yes")])
        write_worklist(self.wl, [row(email="bad@x.com"), row(email="risky@x.com"),
                                 row(email="unk@x.com"), row(email="aa@x.com")])
        self.assertEqual(vq.sweep(apply=True, limit=0, worklist=self.wl, key="k", lock_path=None), 0)
        out = {r["email"]: r for r in read_worklist(self.wl)}
        self.assertEqual(out["bad@x.com"]["status"], "skipped")
        self.assertEqual(out["bad@x.com"]["verify_status"], "undeliverable")
        self.assertIn("verify:undeliverable", out["bad@x.com"]["notes"])
        for e, vs in (("risky@x.com", "risky"), ("unk@x.com", "unknown"),
                      ("aa@x.com", "deliverable_acceptall")):
            self.assertEqual(out[e]["status"], "", f"{e} must stay sendable")
            self.assertEqual(out[e]["verify_status"], vs)
            self.assertEqual(out[e]["verify_date"], date.today().isoformat())

    def test_cull_ceiling_aborts_and_writes_nothing(self):
        n = vq.CULL_MIN_N + 5
        vq.vb = StubVendor([("bad", "undeliverable", "rejected acceptAll=no")] * n)
        write_worklist(self.wl, [row(email=f"u{i}@x.com") for i in range(n)])
        before = open(self.wl).read()
        self.assertEqual(vq.sweep(apply=True, limit=0, worklist=self.wl, key="k", lock_path=None), 1)
        self.assertEqual(open(self.wl).read(), before, "abort must write nothing")

    def test_below_cull_min_n_still_writes(self):
        """A small all-bad batch is not enough evidence to call it a vendor incident."""
        n = 3
        vq.vb = StubVendor([("bad", "undeliverable", "rejected acceptAll=no")] * n)
        write_worklist(self.wl, [row(email=f"u{i}@x.com") for i in range(n)])
        self.assertEqual(vq.sweep(apply=True, limit=0, worklist=self.wl, key="k", lock_path=None), 0)
        self.assertTrue(all(r["status"] == "skipped" for r in read_worklist(self.wl)))

    def test_hand_edited_rows_survive_untouched(self):
        vq.vb = StubVendor([("bad", "undeliverable", "rejected acceptAll=no")])
        write_worklist(self.wl, [row(email="keep@x.com", status="replied", notes="the operator's note"),
                                 row(email="new@x.com")])
        vq.sweep(apply=True, limit=0, worklist=self.wl, key="k", lock_path=None)
        out = {r["email"]: r for r in read_worklist(self.wl)}
        self.assertEqual(out["keep@x.com"]["status"], "replied")
        self.assertEqual(out["keep@x.com"]["notes"], "the operator's note")
        self.assertEqual(out["keep@x.com"]["verify_status"], "")
        self.assertEqual(vq.vb.calls, ["new@x.com"])

    def test_no_key_writes_nothing(self):
        vq.vb = StubVendor([])
        write_worklist(self.wl, [row(email="a@x.com")])
        before = open(self.wl).read()
        self.assertEqual(vq.sweep(apply=True, limit=0, worklist=self.wl, key="", lock_path=None), 1)
        self.assertEqual(open(self.wl).read(), before)

    def test_a_send_landing_mid_verify_is_not_reverted(self):
        """sweep() must re-read before writing: it holds a stale snapshot across the API window.

        Simulates send_outreach._mark_sent landing a status=sent between the read and the write.
        Rewriting the original snapshot would silently revert it.
        """
        wl = self.wl
        write_worklist(wl, [row(email="a@x.com", owner_repo="o/a"),
                            row(email="b@x.com", owner_repo="o/b")])

        class Racing(StubVendor):
            def v_bouncer(self, email, key):
                # while we are "calling the API", the sender marks the OTHER row sent
                cur = read_worklist(wl)
                for r in cur:
                    if r["email"] == "b@x.com":
                        r["status"] = "sent"; r["contacted_on"] = "2026-07-27"
                write_worklist(wl, cur)
                return super().v_bouncer(email, key)

        vq.vb = Racing([("good", "deliverable", "accepted acceptAll=no")] * 2)
        vq.sweep(apply=True, limit=0, worklist=wl, key="k", lock_path=None)
        out = {r["email"]: r for r in read_worklist(wl)}
        self.assertEqual(out["b@x.com"]["status"], "sent", "a send landing mid-run was reverted")
        self.assertEqual(out["b@x.com"]["contacted_on"], "2026-07-27")
        self.assertEqual(out["a@x.com"]["verify_status"], "deliverable")

    def test_lock_held_by_sender_is_a_no_op(self):
        import fcntl
        lockp = os.path.join(self.tmp, ".send.lock")
        held = open(lockp, "w"); fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        vq.vb = StubVendor([("bad", "undeliverable", "rejected acceptAll=no")])
        write_worklist(self.wl, [row(email="a@x.com")])
        before = open(self.wl).read()
        rc = vq.sweep(apply=True, limit=0, worklist=self.wl, key="k", lock_path=lockp)
        self.assertEqual(rc, 0)
        self.assertEqual(vq.vb.calls, [], "must not call the vendor while the sender holds the lock")
        self.assertEqual(open(self.wl).read(), before)
        fcntl.flock(held, fcntl.LOCK_UN); held.close()

    def test_all_columns_preserved_on_write(self):
        vq.vb = StubVendor([("good", "deliverable", "accepted acceptAll=no")])
        write_worklist(self.wl, [row(email="a@x.com", segment="B_all_bot", owner_repo="o/r")])
        vq.sweep(apply=True, limit=0, worklist=self.wl, key="k", lock_path=None)
        with open(self.wl, newline="") as f:
            self.assertEqual(list(csv.DictReader(f).fieldnames), FIELDS)


if __name__ == "__main__":
    unittest.main()

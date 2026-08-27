#!/usr/bin/env python3
"""Tests for the breaker-off switch and the adaptive throttle that replaces it.

The breaker halted on a trailing rate; the throttle scales the daily cap instead. These pin the
two behaviours that matter: breaker_enabled:false never halts on any bounce rate, and the throttle
does not act on a sample too small to support a decision.
"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import send_outreach as S


def cfg(**kw):
    base = {"bounce_window": 100, "bounce_rate_halt": 0.06, "bounce_burst": [4, 10],
            "throttle_window": 100, "throttle_min_n": 40, "adaptive_throttle": True,
            "daily_cap": 40}
    base.update(kw)
    return base


def log(n, bad=0):
    """n live sends, the first `bad` of which are to bounced addresses."""
    return ([{"to": f"bad{i}@x.com", "ts": "2026-07-27T09:00:00"} for i in range(bad)]
            + [{"to": f"ok{i}@x.com", "ts": "2026-07-27T09:00:00"} for i in range(n - bad)])


class Bounces:
    """Patches send_outreach.read_csv so BOUNCES resolves to a fixed hard-bounce set."""
    def __init__(self, emails):
        self.emails = emails

    def __enter__(self):
        self._real = S.read_csv
        S.read_csv = lambda p: ([{"email": e, "type": "hard"} for e in self.emails]
                                if p == S.BOUNCES else self._real(p))
        return self

    def __exit__(self, *a):
        S.read_csv = self._real


class TestBreakerDisabled(unittest.TestCase):
    def test_disabled_never_halts_even_at_a_rate_that_used_to_trip(self):
        with Bounces([f"bad{i}@x.com" for i in range(20)]):
            self.assertIsNone(S.breaker_reason(cfg(breaker_enabled=False), log(100, bad=20)))

    def test_enabled_still_trips_so_the_rollback_works(self):
        with Bounces([f"bad{i}@x.com" for i in range(7)]):
            r = S.breaker_reason(cfg(breaker_enabled=True), log(100, bad=7))
            self.assertIsNotNone(r); self.assertIn("7/100", r)

    def test_default_is_enabled_when_the_key_is_absent(self):
        """A config predating this change must keep its breaker — fail safe, not open."""
        c = cfg(); c.pop("breaker_enabled", None)
        with Bounces([f"bad{i}@x.com" for i in range(7)]):
            self.assertIsNotNone(S.breaker_reason(c, log(100, bad=7)))

    def test_only_literal_false_disables(self):
        for falsy in (0, "", "false", None):
            with Bounces([f"bad{i}@x.com" for i in range(7)]):
                self.assertIsNotNone(S.breaker_reason(cfg(breaker_enabled=falsy), log(100, bad=7)),
                                     f"{falsy!r} must not disable the breaker")


class TestThrottle(unittest.TestCase):
    def test_small_sample_stays_at_full_speed(self):
        with Bounces(["bad0@x.com"]):
            mult, note = S.throttle_factor(cfg(), log(30, bad=1))
        self.assertEqual(mult, 1.0); self.assertIn("too small", note)

    def test_no_bounces_is_full_speed(self):
        with Bounces([]):
            mult, _ = S.throttle_factor(cfg(), log(100))
        self.assertEqual(mult, 1.0)

    def test_tiers(self):
        for bad, expect in ((3, 1.0), (4, 0.75), (7, 0.5), (12, 0.25), (20, 0.0)):
            with Bounces([f"bad{i}@x.com" for i in range(bad)]):
                mult, note = S.throttle_factor(cfg(), log(100, bad=bad))
            self.assertEqual(mult, expect, f"{bad}/100 -> expected x{expect}, got x{mult} ({note})")

    def test_the_rate_that_deadlocked_the_breaker_now_only_halves_the_cap(self):
        """7/100 = 7% wrote HALT under the breaker. It should throttle, not stop."""
        with Bounces([f"bad{i}@x.com" for i in range(7)]):
            mult, _ = S.throttle_factor(cfg(), log(100, bad=7))
        self.assertEqual(mult, 0.5)
        self.assertEqual(int(round(40 * mult)), 20)

    def test_can_be_switched_off_but_never_silently(self):
        """With the breaker off this is the only bounce control — a disable must be loud."""
        with Bounces([f"bad{i}@x.com" for i in range(50)]):
            mult, note = S.throttle_factor(cfg(adaptive_throttle=False), log(100, bad=50))
        self.assertEqual(mult, 1.0)
        self.assertIn("DISABLED", note)

    def test_note_is_never_empty(self):
        """run() prints the note unconditionally; an empty one would hide the throttle state."""
        cases = [(cfg(), log(100, bad=7), ["bad0@x.com"]),
                 (cfg(), log(10), []),                       # small sample
                 (cfg(), log(100), []),                      # no bounces on record
                 (cfg(adaptive_throttle="true"), log(100), ["bad0@x.com"])]   # truthy-not-True
        for c, lg, b in cases:
            with Bounces(b):
                _, note = S.throttle_factor(c, lg)
            self.assertTrue(note.strip(), f"empty note for {c.get('adaptive_throttle')!r}")

    def test_window_is_trailing(self):
        """Old bounces age out of the window as clean sends accumulate."""
        with Bounces([f"bad{i}@x.com" for i in range(7)]):
            mult, _ = S.throttle_factor(cfg(), log(7, bad=7) + log(100)[:100])
        self.assertEqual(mult, 1.0)

    def test_zero_window_does_not_mean_the_whole_log(self):
        """live_log[-0:] is the ENTIRE log, not an empty window — the guard must clamp it."""
        with Bounces([f"bad{i}@x.com" for i in range(7)]):
            mult, note = S.throttle_factor(cfg(throttle_window=0), log(1000, bad=7))
        self.assertNotIn("last 1000", note)

    def test_blank_email_in_bounces_does_not_poison_the_set(self):
        """One blank-email bounce row would otherwise match every send with a blank `to`."""
        with Bounces(["", "  "]):
            mult, note = S.throttle_factor(cfg(), [{"to": "", "ts": ""}] * 100)
        self.assertEqual(mult, 1.0, f"blank bounce email must not match blank recipients ({note})")

    def test_empty_log_does_not_divide_by_zero(self):
        with Bounces(["bad0@x.com"]):
            mult, _ = S.throttle_factor(cfg(throttle_min_n=0), [])
        self.assertEqual(mult, 1.0)


if __name__ == "__main__":
    unittest.main()

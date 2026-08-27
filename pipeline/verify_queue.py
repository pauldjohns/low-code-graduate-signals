#!/usr/bin/env python3
"""Verify queued addresses against Bouncer before we ever mail them, and record the verdict.

  python3 pipeline/verify_queue.py                     # dry-run: what would be spent, no calls
  python3 pipeline/verify_queue.py --apply             # verify + write
  python3 pipeline/verify_queue.py --apply --limit 50

`validate_emails.check()` proves a DOMAIN accepts mail; it says nothing about whether the MAILBOX
exists. On the Review campaign it passed 457/457 addresses as SENDABLE and 11.1% of what it cleared
hard-bounced, four of six bounces on 2026-07-21 being mailbox-level failures. This closes that gap.

Only `undeliverable` is terminal. Everything else — risky, unknown, deliverable-on-an-accept-all
domain — is RECORDED and stays sendable, so a week of sends produces bounce rates split by verdict
class and the harder rules get set from this campaign's own outcomes instead of a small sample.
See method/VERIFY-INTEGRATION.md.

Deliberately its OWN chain step rather than part of ceiling_poll: verification inside the sourcing
transaction means a mid-run vendor error writes status=skipped across the batch and locks those
rows out of the campaign permanently (a real drafting error caught in the Review repo). Here an API
failure costs nothing — rows stay unverified and the next cycle picks them up.

Touches ONLY blank-status rows, never the operator's tracking edits. Atomic .tmp + replace. Dry-run by
default. Idempotent: a row that already carries a settled verdict with a reason is never re-billed.

No ESP host-cull here, unlike the Review original: this worklist has no `esp` column. GitHub
sourcing yields mostly personal and freemail addresses, not corporate gateways, so the Mimecast /
Barracuda pre-send cull would be an inert branch. Revisit only if gateway-hosted domains show up.
"""
import argparse, csv, fcntl, json, os, sys
from collections import Counter
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from send_outreach import norm, LOCK
import verify_bouncer as vb

WORKLIST = os.path.join(ROOT, "outreach", "worklist_ceiling.csv")
VERIFY_COLS = ("verify_status", "verify_reason", "verify_date")
# Chronically-`unknown` addresses (greylisting, vendor timeouts) are re-verified, but not forever:
# without a cooldown a persistently-unknown row is re-billed every poll cycle for no new
# information. At the ~hourly chain cadence that is ~24 charges/address/day, indefinitely.
UNKNOWN_RECHECK_DAYS = 7
# The one verdict this program is willing to act on terminally. Anything else is recorded only.
TERMINAL = "undeliverable"
# If more than this share of a batch comes back undeliverable, something is wrong with the key, the
# vendor, or the status vocabulary — not with the list. Abort rather than cull the queue.
CULL_CEILING = 0.40
CULL_MIN_N = 20
PRICE_PER_ADDRESS = 0.008          # 1k tier, for the dry-run spend projection only


def _parse_detail(detail):
    """v_bouncer packs 'reason acceptAll=X provider=Y' into one string. Pull (accept_all, reason)."""
    accept_all = "acceptAll=yes" in (detail or "")
    reason = (detail or "").split(" acceptAll=")[0].strip()
    return accept_all, reason


def classify(raw_status, accept_all):
    """Bouncer's verdict -> what we store. Unrecognised statuses record, never cull.

    accept_all is kept OUT of the terminal decision on purpose: Bouncer abstained on 0% of Google
    rows while Apollo flagged 46.8% of the same rows accept-all. Two free signals that disagree
    systematically. Recording both is how that gets settled; acting on either now is guessing.
    """
    st = (raw_status or "").strip().lower()
    if st == "deliverable":
        return "deliverable_acceptall" if accept_all else "deliverable"
    if st in ("undeliverable", "risky", "unknown"):
        return st
    return st or "unknown"             # a new vendor status records as itself, never culls


def _load_segments(worklist):
    """Sending segments from the config next to the worklist. Empty set = no segment filter."""
    cfg = os.path.join(os.path.dirname(worklist), "send", "config.json")
    try:
        return {str(s).strip() for s in json.load(open(cfg)).get("segments", []) if str(s).strip()}
    except (OSError, ValueError):
        return set()


def _needs_verify(r, segments=()):
    """Eligible for a (re)verify this run. Blank status only, never the operator's tracking edits.

    Rows outside the sender's enabled `segments` are skipped: credits are finite (the first full
    pass hit HTTP 402 mid-run on 2026-07-27) and there is no reason to pay to verify an address
    select() will never mail. Today that is C_mover_fresh, ~61 rows. If a segment is enabled later
    its rows simply become eligible on the next pass.

    Re-verify beyond a never-seen row in two cases: a verdict of 'unknown' (Bouncer's
    transient/greylist class — freezing that as final on one timeout is a bug), and a row carrying
    a status but no reason (verified before the reason column existed). A settled
    deliverable/undeliverable/risky row with a reason on file is never re-billed.
    """
    if (r.get("status") or "").strip():
        return False
    if not norm(r.get("email")):
        return False
    if segments and r.get("segment") not in segments:
        return False
    vs = (r.get("verify_status") or "").strip()
    if not vs:
        return True
    if vs == "unknown":
        return _older_than(r.get("verify_date"), UNKNOWN_RECHECK_DAYS)
    if not (r.get("verify_reason") or "").strip():
        return True
    return False


def _older_than(datestr, days):
    """True when `datestr` (ISO date) is missing, unparseable, or at least `days` old."""
    try:
        seen = datetime.strptime((datestr or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return True                      # never stamped, or corrupt — treat as due
    return (date.today() - seen) >= timedelta(days=days)


def verify_rows(rows, key, limit, segments=(), log=print):
    """Call Bouncer for eligible rows. Returns (results, errors). Never raises on vendor failure."""
    todo = [r for r in rows if _needs_verify(r, segments)]
    if limit:
        todo = todo[:limit]
    results, errors = {}, 0
    seen = set()
    for r in todo:
        e = norm(r["email"])
        if e in seen:          # one founder across N repo rows is one mailbox, not N — don't re-bill
            continue
        seen.add(e)
        verdict, raw, detail = vb.v_bouncer(e, key)
        if verdict == "error":
            # A 402/401/timeout must never be mistaken for a verdict about the mailbox. Stop:
            # continuing would spend the rest of the run re-learning the same failure.
            errors += 1
            log(f"[verify] vendor error on {e}: {raw} — stopping, {len(results)} verified so far")
            break
        accept_all, reason = _parse_detail(detail)
        results[e] = (classify(raw, accept_all), reason)
    return results, errors


def sweep(apply, limit, worklist=WORKLIST, key=None, lock_path=LOCK, segments=None):
    if not os.path.exists(worklist):
        print(f"no worklist at {worklist}"); return 1
    # Same advisory lock send_outreach takes. A hand-run --apply while the loop is live would
    # otherwise race the sender's per-message worklist rewrite; whoever replaces last wins and the
    # other's writes vanish. Non-blocking: if the sender holds it, say so and do nothing.
    if apply and lock_path:
        try:
            _lock_fh = open(lock_path, "w")
            fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            print("[verify] the sender holds the worklist lock — not verifying this pass. "
                  "Re-run when the send finishes (or stop the loop first).")
            return 0
    key = key if key is not None else vb.load_key("BOUNCER_API_KEY")
    segments = _load_segments(worklist) if segments is None else set(segments)
    with open(worklist, newline="") as f:
        rd = csv.DictReader(f); rows = list(rd); fields = list(rd.fieldnames or [])
    missing = [c for c in VERIFY_COLS if c not in fields]
    if missing:
        print(f"[verify] worklist has no {', '.join(missing)} column(s) — they are in ceiling_poll's "
              f"DATA_COLS, so run one poll to migrate the file, then re-run this.")
        return 1

    eligible = [r for r in rows if _needs_verify(r, segments)]
    planned = eligible[:limit] if limit else eligible
    distinct = len({norm(r.get("email")) for r in planned})
    by_seg = Counter((r.get("segment") or "?") for r in planned)
    backfill = sum(1 for r in planned if (r.get("verify_status") or "").strip())
    print(f"worklist: {worklist}")
    if segments:
        print(f"sending segments (others are not verified — credits are finite): {sorted(segments)}")
    print(f"eligible (blank status, needs a verdict or reason): {len(eligible)}")
    print(f"would verify: {distinct} distinct address(es) from {len(planned)} row(s)"
          f"  ~${distinct * PRICE_PER_ADDRESS:.2f} at the 1k tier"
          + (f"  (incl. {backfill} re-verify: unknown or missing reason)" if backfill else ""))
    print(f"  by segment: {dict(by_seg)}")

    if not apply:
        print("DRY-RUN: no API calls, no write. Re-run with --apply.")
        return 0
    if not planned:
        print("nothing to do."); return 0
    if not key:
        print("[verify] no BOUNCER_API_KEY — nothing done. Looked in: $BOUNCER_API_KEY, "
              + ", ".join(vb.ENV_CANDIDATES)); return 1

    results, errors = verify_rows(rows, key, limit, segments)
    # `rows` is now a STALE snapshot: the API window above runs for minutes, and send_outreach's
    # _mark_sent does a full read-modify-write per message for hours. Rewriting the snapshot would
    # silently revert every status=sent / contacted_on / notes edit landed in between. Re-read and
    # merge verdicts by email instead of writing what we read at the top. run_chain.sh serialises
    # this against the sender, but VERIFY-INTEGRATION.md §7 step 5 tells the operator to run
    # --apply by hand, which takes no chain lock at all.
    with open(worklist, newline="") as f:
        rd = csv.DictReader(f); rows = list(rd); fields = list(rd.fieldnames or [])

    counts = Counter(v for v, _ in results.values())
    culls = counts.get(TERMINAL, 0)
    if len(results) >= CULL_MIN_N and culls / len(results) > CULL_CEILING:
        print(f"[verify] ABORT: {culls}/{len(results)} came back {TERMINAL} "
              f"({culls/len(results):.0%} > {CULL_CEILING:.0%} ceiling). That is a bad key, a vendor "
              f"incident or a changed status vocabulary, not a bad list. Nothing written.")
        return 1
    if not results:
        print("[verify] no verdicts returned — nothing written."); return 1

    rundate = date.today().isoformat()
    marked = 0
    for r in rows:
        got = results.get(norm(r.get("email")))
        if not got or (r.get("status") or "").strip():
            continue
        v, reason = got
        r["verify_status"] = v
        r["verify_reason"] = reason
        r["verify_date"] = rundate
        if v == TERMINAL:
            r["status"] = "skipped"
            sep = "; " if (r.get("notes") or "").strip() else ""
            r["notes"] = (r.get("notes") or "") + f"{sep}verify:{v} {rundate}"
            marked += 1

    tmp = worklist + ".verify.tmp"       # NOT the shared .tmp that ceiling_poll/send_outreach use
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    os.replace(tmp, worklist)

    print(f"\nverified {len(results)}: {dict(counts)}")
    print(f"marked skipped: {marked} (only '{TERMINAL}' is terminal; risky/unknown/accept-all stay sendable)")
    if errors:
        print(f"stopped early on a vendor error — {len(eligible) - len(results)} row(s) still "
              f"unverified, they will be picked up next run")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="make the calls and write (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="verify at most N rows this run")
    ap.add_argument("--worklist", default=WORKLIST)
    a = ap.parse_args()
    return sweep(apply=a.apply, limit=a.limit, worklist=a.worklist)


if __name__ == "__main__":
    sys.exit(main())

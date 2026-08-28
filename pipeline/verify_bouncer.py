#!/usr/bin/env python3
"""Bouncer mailbox-verification adapter. Ported from the sibling engine's verify_bakeoff.py
(the exact adapter every calibration number in method/VERIFY-INTEGRATION.md came from).

Only the adapter is ported. The bake-off harness it lived in is Review-specific: it scores vendors
against that campaign's six known bounces, which say nothing about this list.

Key, mode 600, outside the repo. Lookup order (first hit wins):

  1. the BOUNCER_API_KEY environment variable
  2. ~/.config/graduate-signals/verify.env   engine-local override; does not exist today
  3. ~/.config/shared-outreach/verify.env    the SHARED secrets dir, where the key actually lives
                                             (alongside apollo.env and the Google OAuth files)

the operator, 2026-07-27: point this engine at the shared file rather than minting a second key —
**Bouncer credits are per-key, so a duplicate splits the usage accounting.**

This deliberately overrides method/VERIFY-INTEGRATION.md §2, which said to use a separate
~/.config/graduate-signals/ dir by analogy with the split Gmail token dirs. That analogy does not
hold: the split exists because `go.sh` does `rm -f "$TOKEN"` on re-consent, and TOKEN is the
token.json path specifically — verify.env was never in its blast radius. Slot 2 is kept ahead of
slot 3 so the engines can be split later by dropping a file in, with no code change.

Note the lookup has to live HERE and not in a shell shim: run_chain.sh invokes verify_queue.py
directly, so a `set -a; . verify.env; set +a` in an interactive shell never reaches the loop.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request

ENV_CANDIDATES = [os.path.expanduser("~/.config/graduate-signals/verify.env"),
                  os.path.expanduser("~/.config/shared-outreach/verify.env")]
ENV = ENV_CANDIDATES[-1]          # the one in use today, for error messages


def _from_file(path, name):
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def load_key(name):
    """Env var wins, then each candidate file in order. Empty string when absent — callers check."""
    k = os.environ.get(name, "").strip()
    if k:
        return k
    for path in ENV_CANDIDATES:
        k = _from_file(path, name)
        if k:
            return k
    return ""


def _get(url, headers=None, tries=3):
    """GET -> parsed JSON, or {"_error": reason}. Never raises: a vendor failure is not a verdict."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt * 2); continue
            return {"_error": f"HTTP{e.code}"}
        except Exception as e:
            if attempt < tries - 1:
                time.sleep(2 ** attempt * 2); continue
            return {"_error": type(e).__name__}
    return {"_error": "exhausted"}


def v_bouncer(email, key):
    """(normalised_verdict, raw_status, detail).

    Normalised vocabulary: bad = do not send · good = safe to send · unsure = the vendor is
    abstaining (catch-all / unknown / risky). Abstention is a THIRD outcome on purpose. Collapsing
    it into good is the failure mode that makes a verifier useless; collapsing it into bad silently
    deletes half the ICP. verify_queue.classify() reads raw_status, not this verdict, and only
    'undeliverable' is ever terminal.
    """
    r = _get("https://api.usebouncer.com/v1.1/email/verify?"
             + urllib.parse.urlencode({"email": email, "timeout": 30}),
             {"x-api-key": key})
    if "_error" in r:
        return "error", r["_error"], ""
    st = (r.get("status") or "").lower()
    detail = " ".join(x for x in [r.get("reason") or "",
                                  f"acceptAll={(r.get('domain') or {}).get('acceptAll')}",
                                  f"provider={r.get('provider') or '?'}"] if x)
    return ({"undeliverable": "bad", "deliverable": "good"}.get(st, "unsure"), st, detail)

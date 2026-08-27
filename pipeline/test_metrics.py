#!/usr/bin/env python3
"""test_metrics.py - offline test for account-signup attribution. The dashboard undercounted
because it matched a signup's email to the exact address we cold-emailed; people sign up with a
DIFFERENT email. Attribution must instead map a signup to a sent REPO via (a) the creator email,
(b) the repo owner's handle, or (c) a human-verified worklist 'signup' note. No network.
Run: python3 pipeline/test_metrics.py"""
import os, sys, csv, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_metrics as M

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name}")

def _write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header); w.writeheader(); w.writerows(rows)

tmp = tempfile.mkdtemp()
SEND = os.path.join(tmp, "send_log.csv"); WL = os.path.join(tmp, "worklist.csv")
_write(SEND, ["ts", "to", "owner_repo", "segment", "mode"], [
    {"ts": "2026-07-12T08:00:00", "to": "ownerone@gmail.com", "owner_repo": "ownerone/harvest-app", "segment": "B_all_bot", "mode": "live"},
    {"ts": "2026-07-13T08:00:00", "to": "ownertwo@gmail.com", "owner_repo": "ownertwo/CleanCo", "segment": "B_all_bot", "mode": "live"},
    {"ts": "2026-07-10T08:00:00", "to": "ownerthreealt@gmail.com", "owner_repo": "ownerthree/scan-style-app", "segment": "A_hybrid", "mode": "live"},
    {"ts": "2026-07-14T08:00:00", "to": "someone@x.com", "owner_repo": "someone/quiet-app", "segment": "C_mover", "mode": "pending"},  # not live
])
_write(WL, ["owner_repo", "email", "contact_paths", "notes"], [
    {"owner_repo": "ownerone/harvest-app", "email": "ownerone@gmail.com", "contact_paths": "", "notes": ""},
    {"owner_repo": "ownertwo/CleanCo", "email": "ownertwo@gmail.com", "contact_paths": "commit_email:ownertwo@gmail.com",
     "notes": "LIKELY SIGNUP: product org 'CleanCo Technologies Ltd' by colleague.one@gmail.com (product-name match)"},
    {"owner_repo": "ownerthree/scan-style-app", "email": "ownerthreealt@gmail.com", "contact_paths": "", "notes": "REPLIED"},
])
M.SEND_LOG = SEND; M.WORKLIST = WL

sent, repos, notes_signup, daily = M.load_sends()
check("only live sends counted (pending excluded)", set(sent) == {"ownerone@gmail.com", "ownertwo@gmail.com", "ownerthreealt@gmail.com"})
check("repo owner handle in identity (ownerthree)", "ownerthree" in repos["ownerthree/scan-style-app"]["handles"])
check("notes 'LIKELY SIGNUP' flags CleanCo", "ownertwo/CleanCo" in notes_signup)
check("plain 'REPLIED' note is NOT a signup flag", "ownerthree/scan-style-app" not in notes_signup)

# replicate the reverse-index matching gmail_signals uses (no network)
email_to_repo = {}; handle_to_repo = {}
for repo, d in repos.items():
    for e in d["emails"]: email_to_repo.setdefault(e, repo)
    for h in d["handles"]:
        if h: handle_to_repo.setdefault(h, repo)
def match(creator):
    return email_to_repo.get(M.norm(creator)) or handle_to_repo.get(M._slug(creator.split("@")[0]))

check("exact-email signup matches (exact-email match)", match("ownerone@gmail.com") == "ownerone/harvest-app")
check("different-email owner-handle signup matches (owner-handle match)", match("ownerthree@gmail.com") == "ownerthree/scan-style-app")
check("unrelated signup does NOT match (no false positive)", match("randomvc@calendly.com") is None)
# CleanCo: creator email is neither the sent email nor a handle -> only the NOTE catches it
check("CleanCo not matchable by email/handle (needs the note)", match("colleague.one@gmail.com") is None)
account_repos = {match("ownerone@gmail.com"), match("ownerthree@gmail.com")} | notes_signup
account_repos.discard(None)
check("union(email/handle, notes) = 3 converted repos", account_repos == {"ownerone/harvest-app", "ownerthree/scan-style-app", "ownertwo/CleanCo"})

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

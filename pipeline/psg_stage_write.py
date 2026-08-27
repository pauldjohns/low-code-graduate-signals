#!/usr/bin/env python3
"""Stage the first PSG batch into the LIVE worklist + enable segments. Quiesces the engine
(STOP + chain lock) so no poll/send races the write. Dry by default; pass --commit to write.
  python3 psg_stage_write.py            # preview only
  python3 psg_stage_write.py --commit   # quiesce, upsert 15, flip config, verify, resume"""
import csv, os, re, sys, time, json

MAIN = os.environ.get("MAIN_CHECKOUT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(MAIN,"pipeline"))
import ceiling_poll as cp     # ROOT -> MAIN; worklist_upsert targets the LIVE worklist
import send_outreach as S
SURV_CSV = f"{MAIN}/outreach/send/psg_holdback.csv"   # durable survivor list (edge-filtered PSG)
COMMIT = "--commit" in sys.argv
N_STAGE = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--n=")), "15"))
CFG = f"{MAIN}/outreach/send/config.json"
SEND = f"{MAIN}/outreach/send"
LOCKDIR = f"{SEND}/.chain.lock.d"
ADD_SEGMENTS = ["A_hybrid","C_mover","presumed_silent_graduate"]

# --- junk/placeholder email filter (enrichment can pick up committed placeholder commit emails) ---
JUNK_LOCAL = {"your","youremail","your-email","test","example","user","username","name","email",
              "admin","demo","sample","placeholder","changeme","none","na","noreply","email address"}
JUNK_DOMAIN = ("email.com","example.com","example.org","test.com","domain.com","yourdomain.com",
               "yourcompany.com","company.com","mydomain.com","sample.com","placeholder.com")
def is_junk(email):
    e=S.norm(email); loc=e.split("@")[0]; dom=e.split("@")[-1]
    return (loc in JUNK_LOCAL) or dom.endswith(JUNK_DOMAIN) or "your" in loc and "email" in loc

surv=list(csv.DictReader(open(SURV_CSV)))
clean=[r for r in surv if not is_junk(r["email"])]
dropped=[r for r in surv if is_junk(r["email"])]
batch=clean[:N_STAGE]

print(f"survivors: {len(surv)} | junk-filtered out: {len(dropped)} ({[r['email'] for r in dropped][:6]})")
print(f"clean survivors: {len(clean)} | staging first {len(batch)} (holding {len(clean)-len(batch)})\n")
print("STAGED BATCH:")
for r in batch:
    print(f"  {r['email'][:34]:34} | {r['region']:8} | {(r['edge_hosts']+' '+r['edge_secrets'])[:44]}")

def build_rows(batch):
    out=[]
    for r in batch:
        out.append({"first_seen":r.get("first_seen",""),"segment":"presumed_silent_graduate","trend":"",
            "bot_total":"","true_age_days":"","c30_bot":"","c30_local":"","last_bot":"","reverts_in_100":"",
            "signal_deps":r.get("edge_marker",""),"product":"","live_url":"","email":r["email"],
            "x_handle":r.get("x_handle",""),"contact_paths":"","owner_location":r.get("owner_location",""),
            "owner_repo":r["owner_repo"],"github_url":f"https://github.com/{r['owner_repo']}"})
    return out

if not COMMIT:
    print(f"\n[DRY] would upsert {len(batch)} PSG rows and set segments -> {['B_all_bot']+ADD_SEGMENTS}")
    print("[DRY] re-run with --commit to execute.")
    sys.exit(0)

# ---- COMMIT PATH: quiesce, write, verify, resume ----
print("\n[commit] quiescing engine...")
open(f"{SEND}/STOP","w").close()                     # stop sends
# grab the whole-cycle lock so no poll/send writes the worklist during our upsert
waited=0
while waited < 900:
    try:
        os.mkdir(LOCKDIR); open(f"{LOCKDIR}/pid","w").write(str(os.getpid())); break
    except FileExistsError:
        opid=(open(f"{LOCKDIR}/pid").read().strip() if os.path.exists(f"{LOCKDIR}/pid") else "")
        dead=False
        if opid:
            try: os.kill(int(opid),0)                     # macOS-safe liveness (no /proc)
            except (ProcessLookupError, ValueError): dead=True
            except PermissionError: pass                  # alive, just not ours
        if dead:                                          # stale lock from a crashed run — clear + retry now
            try: os.remove(f"{LOCKDIR}/pid")
            except OSError: pass
            try: os.rmdir(LOCKDIR)
            except OSError: pass
            continue
        print(f"  chain lock held (pid {opid or '?'}); waiting... ({waited}s)"); time.sleep(15); waited+=15
else:
    print("[commit] ABORT: chain lock never freed in 900s. No changes made. (STOP left set — rm to resume.)"); sys.exit(1)

try:
    # re-dedup vs the CURRENT worklist + send_log at write time (belt-and-suspenders)
    wl_now={S.norm(r.get("email")) for r in csv.DictReader(open(f"{MAIN}/outreach/worklist_ceiling.csv")) if r.get("email")}
    sl_now={S.norm(r.get("to")) for r in csv.DictReader(open(f"{MAIN}/outreach/send/send_log.csv")) if r.get("mode") in ("live","pending")}
    final=[r for r in batch if S.norm(r["email"]) not in wl_now and S.norm(r["email"]) not in sl_now]
    rows=build_rows(final)
    added,total=cp.worklist_upsert(rows)
    print(f"[commit] worklist_upsert: +{added} rows -> {total} total")

    # flip config: add segments (idempotent), assert dry_run untouched
    c=json.load(open(CFG))
    assert c["dry_run"] is False and c["template_approved"] is True, "REFUSING: live config not armed!"
    before=list(c["segments"])
    for s in ADD_SEGMENTS:
        if s not in c["segments"]: c["segments"].append(s)
    tmp=CFG+".tmp"; json.dump(c, open(tmp,"w"), indent=1); os.replace(tmp, CFG)
    print(f"[commit] segments: {before} -> {c['segments']}  (dry_run still {c['dry_run']})")
finally:
    if os.path.exists(LOCKDIR):
        try: os.remove(f"{LOCKDIR}/pid")
        except FileNotFoundError: pass
        os.rmdir(LOCKDIR)
    os.remove(f"{SEND}/STOP")                          # resume sending
    print("[commit] released lock + cleared STOP (engine resumes next cycle).")

# verify
wl=list(csv.DictReader(open(f"{MAIN}/outreach/worklist_ceiling.csv")))
psg_in_wl=[r for r in wl if r.get("segment")=="presumed_silent_graduate"]
c=json.load(open(CFG))
print(f"\n[verify] PSG rows now in live worklist: {len(psg_in_wl)}")
print(f"[verify] config segments: {c['segments']}  dry_run={c['dry_run']} template_approved={c['template_approved']}")

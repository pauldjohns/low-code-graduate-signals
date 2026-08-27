#!/usr/bin/env python3
"""test_send_dedup.py - within-run duplicate-email dedup in select(). A founder with several
Lovable repos produces multiple worklist rows sharing one email; they hash to the same tz slot
and would all fire in the SAME run (observed live: one address sent 3x in one run_id). select()
must collapse them to one send. No network. Run: python3 pipeline/test_send_dedup.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import send_outreach as S

PASS=0; FAIL=0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS+=1; print(f"  ok   {name}")
    else: FAIL+=1; print(f"  FAIL {name}")

cfg={"segments":[]}   # empty segment list + no role/region/tz/valid flags -> isolate dedup behavior
rows=[{"email":"dup@x.com","owner_repo":"a/one","status":""},
      {"email":"dup@x.com","owner_repo":"a/two","status":""},
      {"email":"other@x.com","owner_repo":"a/three","status":""}]

out=S.select(rows, cfg, set(), set(), 100, None)
check("two rows same email -> one selected", len(out)==2 and {S.norm(r["email"]) for r in out}=={"dup@x.com","other@x.com"})

out2=S.select(rows, cfg, set(), {"dup@x.com"}, 100, None)   # already-sent (send_log) still excluded
check("already-sent email excluded", len(out2)==1 and out2[0]["email"]=="other@x.com")

out3=S.select([rows[0]]*3, cfg, set(), set(), 100, None)    # 3 identical rows -> one
check("three identical rows -> one selected", len(out3)==1)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

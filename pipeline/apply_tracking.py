#!/usr/bin/env python3
"""
apply_tracking.py - push dashboard tracking (tracking.json) back into the worklist CSVs.
The daily build_worklist.py preserves these columns, so once applied they survive future runs.
Usage: python3 pipeline/apply_tracking.py ~/Downloads/tracking.json [YYYY-MM-DD]
"""
import csv, json, os, sys, datetime
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
track=json.load(open(os.path.expanduser(sys.argv[1])))
when=sys.argv[2] if len(sys.argv)>2 else datetime.date.today().isoformat()

def apply(path, key, date_col):
    fp=os.path.join(ROOT,path)
    if not os.path.exists(fp): return 0
    rows=list(csv.DictReader(open(fp))); cols=rows[0].keys() if rows else []; n=0
    for r in rows:
        t=track.get(r.get(key,""))
        if not t: continue
        if t.get("messaged"):
            if not r.get("status"): r["status"]="contacted"
            if not r.get(date_col): r[date_col]=when
        if t.get("notes"): r["notes"]=t["notes"]
        n+=1
    with open(fp,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(cols)); w.writeheader(); w.writerows(rows)
    return n

a=apply("outreach/worklist_repo.csv","owner_repo","contacted_on")
b=apply("outreach/worklist_social.csv","post_url","engaged_on")
c=apply("outreach/worklist_ceiling.csv","owner_repo","contacted_on")
print(f"applied tracking: {a} repo rows, {b} social rows, {c} ceiling rows updated (status/date/notes).")
print("re-run build_dashboard.py to refresh the HTML from the updated worklists.")

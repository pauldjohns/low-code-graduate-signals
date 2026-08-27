#!/usr/bin/env python3
"""
Dedup store for Lovable graduates. Canonical key = owner/repo.
Path-aware: store lives at <project>/store/seen_store.csv regardless of cwd.

Usage (from anywhere):
  python3 pipeline/dedup_store.py stats
  python3 pipeline/dedup_store.py merge <csv> <run> <date>   # writes <csv>.netnew.csv, prints net-new
  python3 pipeline/dedup_store.py backfill                   # one-time genesis (already run 2026-06-24)
"""
import csv, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORE = os.path.join(ROOT, "store", "seen_store.csv")
FIELDS = ["owner_repo","first_seen","last_seen","times_seen","tier","classification",
          "region_bucket","country","confidence","platform","live_verified","destination",
          "best_contact","product_oneline","source_runs"]

def load():
    if not os.path.exists(STORE): return {}
    return {r["owner_repo"]: r for r in csv.DictReader(open(STORE))}

def save(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader()
        for k in sorted(store): w.writerow({c:store[k].get(c,"") for c in FIELDS})

def g(row,*names):
    for n in names:
        v=row.get(n)
        if v not in (None,""): return v
    return ""

def upsert(store, rec, run, date):
    """rec: dict with possibly-varied column names. Returns True if NET-NEW."""
    repo = g(rec,"owner_repo")
    if not repo: return False
    fm = {"tier": g(rec,"tier"), "classification": g(rec,"classification"),
          "region_bucket": g(rec,"region_bucket"), "country": g(rec,"country","country_guess"),
          "confidence": g(rec,"confidence"), "platform": g(rec,"platform"),
          "live_verified": g(rec,"live_verified"), "destination": g(rec,"destination"),
          "best_contact": g(rec,"best_contact","emails"), "product_oneline": g(rec,"product_oneline","description")}
    if repo not in store:
        store[repo] = {"owner_repo":repo,"first_seen":date,"last_seen":date,"times_seen":"1",
                       "source_runs":run, **fm}
        return True
    s = store[repo]
    s["last_seen"] = max(s.get("last_seen",date), date)
    runs = set(filter(None, s.get("source_runs","").split(";")))
    if run not in runs:
        runs.add(run); s["source_runs"]=";".join(sorted(runs)); s["times_seen"]=str(len(runs))
    for k,v in fm.items():
        if v and not s.get(k): s[k]=v
    return False

def merge_csv(path, run, date, write_netnew=True):
    store=load(); rows=list(csv.DictReader(open(path)))
    netnew=[r for r in rows if upsert(store,r,run,date)]
    save(store)
    out=None
    if write_netnew and netnew:
        out=path.rsplit(".",1)[0]+".netnew.csv"
        with open(out,"w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(netnew)
    return len(rows), len(netnew), out

def backfill():
    if os.path.exists(STORE): os.remove(STORE)
    L=os.path.join(ROOT,"leads"); E=os.path.join(L,"earlier-runs"); D=os.path.join(ROOT,"data")
    sources=[(os.path.join(L,"region_tagged_full.csv"),"full-1k"),(os.path.join(L,"graduates_full.csv"),"full-1k"),
             (os.path.join(E,"region_tagged.csv"),"pool-94"),(os.path.join(E,"graduates_lastweek_webonly.csv"),"lastweek"),
             (os.path.join(E,"graduates.csv"),"main-58"),(os.path.join(D,"candidates_full.csv"),"full-1k"),
             (os.path.join(D,"candidates_repo.csv"),"main-58"),(os.path.join(D,"candidates_lastweek.csv"),"lastweek")]
    store=load()
    for path,run in sources:
        if not os.path.exists(path): continue
        n=0
        for r in csv.DictReader(open(path)): upsert(store,r,run,"2026-06-24"); n+=1
        print(f"  ingested {n:3} from {os.path.relpath(path,ROOT)} ({run})")
    save(store); print(f"store size: {len(store)} unique repos -> {os.path.relpath(STORE,ROOT)}")

def stats():
    store=load(); from collections import Counter
    print(f"store: {len(store)} unique repos ({os.path.relpath(STORE,ROOT)})")
    print(" region:", dict(Counter(v.get('region_bucket') or 'untagged' for v in store.values())))
    print(" tier:  ", dict(Counter(v.get('tier') or 'unjudged' for v in store.values())))
    print(" times_seen:", dict(Counter(v.get('times_seen','1') for v in store.values())))

if __name__=="__main__":
    cmd = sys.argv[1] if len(sys.argv)>1 else "stats"
    if cmd=="backfill": backfill(); stats()
    elif cmd=="merge":
        total,new,out = merge_csv(sys.argv[2], sys.argv[3], sys.argv[4])
        print(f"merged {sys.argv[2]}: {total} rows, {new} NET-NEW" + (f" -> {out}" if out else ""))
    else: stats()

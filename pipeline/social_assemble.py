#!/usr/bin/env python3
"""
social_assemble.py - turn social-classify output into deliverables + dedup store.
Usage: python3 pipeline/social_assemble.py <classify_output.json> <rundate> <social_raw.json>
Writes: leads/social_leads.csv (all warm, deduped per author, scored),
        leads/social_contactable.csv (warm AND identity-resolvable - the outreach shortlist),
        leads/social_wedge.md, store/social_seen.csv (url-keyed, with `contacted` state).
"""
import csv, json, os, sys
from collections import Counter
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
LEADS=os.path.join(ROOT,"leads"); STORE=os.path.join(ROOT,"store","social_seen.csv")
data=json.load(open(sys.argv[1])); items=data.get("result",data)
rundate=sys.argv[2] if len(sys.argv)>2 else "unknown"
raw={r["url"]:r for r in json.load(open(sys.argv[3]))} if len(sys.argv)>3 else {}

def clean_handle(it):
    """Prefer the clean author_url from the raw pull; fix reddit /user/ form."""
    r=raw.get(it.get("url"),{})
    au=r.get("author_url","")
    if au: return au
    return it.get("outreach_handle","")

for x in items:
    x["outreach_handle"]=clean_handle(x)
    x["platform"]=x.get("source","").split(":")[0]
    x["post_date"]=(raw.get(x.get("url"),{}).get("date") or "")[:10]   # publish date -> staleness

CL={"graduate_self_solver":0,"in_market_complaint":1,"rescue_seeker":2,"quitter":3,"happy_stayer":4,"noise":5}

# author-dedup: one row per (platform, author), keep the highest-scoring / best-classified item
best={}
for x in items:
    key=(x["platform"], x.get("author",""))
    cur=best.get(key)
    score=int(x.get("warm_score") or 0)
    if cur is None or (score, -CL.get(x.get("classification"),9)) > (int(cur.get("warm_score") or 0), -CL.get(cur.get("classification"),9)):
        best[key]=x
deduped=list(best.values())

warm=[x for x in deduped if x.get("is_warm_lead") and x.get("classification") not in ("noise","happy_stayer")]
warm.sort(key=lambda x:(-int(x.get("warm_score") or 0), CL.get(x.get("classification"),9)))
cols=["warm_score","contactable","classification","platform","post_date","author","trigger","destination","outreach_handle","wedge_quote","url"]
def dump(fn,rows):
    with open(os.path.join(LEADS,fn),"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
dump("social_leads.csv", warm)
contactable=[x for x in warm if str(x.get("contactable")).lower()=="true"]
dump("social_contactable.csv", contactable)

# wedge (from all items with a quote, deduped by url)
seenq=set(); wedge=[]
for x in items:
    if (x.get("wedge_quote") or "").strip() and x["url"] not in seenq:
        seenq.add(x["url"]); wedge.append(x)
with open(os.path.join(LEADS,"social_wedge.md"),"w") as f:
    f.write(f"# Social wedge language (verbatim)\n\n_Generated {rundate}. Reddit + Hacker News + Dev.to + Mastodon + Bluesky. Builders' own words on leaving Lovable / what they want._\n\n")
    for cl in ["graduate_self_solver","in_market_complaint","quitter","rescue_seeker"]:
        g=[x for x in wedge if x["classification"]==cl]
        if not g: continue
        f.write(f"## {cl} ({len(g)})\n\n")
        for x in g:
            f.write(f"- “{x['wedge_quote'].strip()}” — {x.get('author')} [{x.get('platform')}] (trigger: {x.get('trigger')}, dest: {x.get('destination')})\n  {x.get('url')}\n")
        f.write("\n")

# dedup store (key=url) with contacted state preserved
store={}
if os.path.exists(STORE):
    store={r["url"]:r for r in csv.DictReader(open(STORE))}
netnew=0
for x in items:
    u=x.get("url")
    if not u: continue
    if u not in store:
        store[u]={"url":u,"first_seen":rundate,"last_seen":rundate,"platform":x["platform"],
                  "author":x.get("author",""),"classification":x.get("classification",""),
                  "warm_score":x.get("warm_score",""),"is_warm_lead":str(x.get("is_warm_lead")),
                  "contactable":str(x.get("contactable")),"outreach_handle":x.get("outreach_handle",""),"contacted":""}
        netnew+=1
    else:
        store[u]["last_seen"]=rundate
os.makedirs(os.path.dirname(STORE),exist_ok=True)
fld=["url","first_seen","last_seen","platform","author","classification","warm_score","is_warm_lead","contactable","outreach_handle","contacted"]
with open(STORE,"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fld,extrasaction="ignore"); w.writeheader()
    for u in store: w.writerow(store[u])

c=Counter(x["classification"] for x in deduped)
print("=== social classify (author-deduped) ===")
for k,v in c.most_common(): print(f"  {k}: {v}")
print(f"\ndistinct people: {len(deduped)} | warm: {len(warm)} | warm+contactable: {len(contactable)} | wedge: {len(wedge)} | store net-new: {netnew}")
print("warm by platform:", dict(Counter(x['platform'] for x in warm)))
print("contactable by platform:", dict(Counter(x['platform'] for x in contactable)))
print("warm_score dist:", dict(Counter(x.get('warm_score') for x in warm)))
print("wrote leads/social_leads.csv, leads/social_contactable.csv, leads/social_wedge.md, store/social_seen.csv")

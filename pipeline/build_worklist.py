#!/usr/bin/env python3
"""
build_worklist.py - maintain the two persistent OUTREACH worklists the operator works from.
APPEND-ONLY upsert: new leads are added; existing rows (and the operator's tracking edits:
status/notes/contacted_on/...) are preserved untouched. Run after each daily pull.

Writes: outreach/worklist_repo.csv   (git-repo graduates to EMAIL; target region only)
        outreach/worklist_social.csv (social handles to REVIEW / COMMENT / ENGAGE)
Idempotent. Key: owner_repo (repo) / post_url (social).
"""
import csv, os
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
OUT=os.path.join(ROOT,"outreach"); os.makedirs(OUT, exist_ok=True)
def rd(p):
    fp=os.path.join(ROOT,p); return list(csv.DictReader(open(fp))) if os.path.exists(fp) else []
def idx(rows,key): return {r.get(key,""):r for r in rows if r.get(key)}

BADMAIL=("noreply","[bot]","bot@","@anthropic.com","@cursor.com","cursoragent","@example.com",
         ".local",".home",".lan","users.noreply","github-actions","actions@","action@","@github.com",
         "privaterelay","@sqi.app")
def email_of(r):
    """First USABLE email across emails + best_contact; skips bot/generic addresses. '' if none."""
    for c in ("emails","best_contact","email","profile_email"):
        v=r.get(c,"") or ""
        for part in v.replace(",",";").split(";"):
            e=part.strip()
            if "@" in e and not any(b in e.lower() for b in BADMAIL):
                return e
    return ""

def upsert(path, key, newrows, track_cols, data_cols):
    existing={r[key]:r for r in (list(csv.DictReader(open(path))) if os.path.exists(path) else [])}
    added=0
    for nr in newrows:
        k=nr.get(key,"")
        if not k: continue
        if k in existing:                            # refresh DATA columns; preserve TRACKING edits
            for dc in data_cols:
                if dc in nr: existing[k][dc]=nr[dc]
        else:
            existing[k]={**{t:"" for t in track_cols}, **nr}; added+=1
    cols=track_cols+data_cols
    with open(path,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore"); w.writeheader()
        for r in existing.values(): w.writerow(r)
    return added, len(existing)

# ---------------- REPO worklist (emails to reach out to; target region only) ----------------
store=idx(rd("store/seen_store.csv"),"owner_repo")
region=idx(rd("leads/region_tagged_full.csv"),"owner_repo")
grads={r["owner_repo"]:r for r in rd("leads/graduates_full.csv") if r.get("tier") in ("A","B")}
ORD={"TARGET":0,"UNKNOWN":1}; TB={"A":0,"B":1}
repo_rows=[]
disq=0
for repo,g in grads.items():
    reg=region.get(repo,{}); bucket=reg.get("region_bucket","UNKNOWN")
    if bucket=="NON_TARGET": continue                 # founder-location filter (the operator's call)
    em=email_of(g)
    if not em: disq+=1; continue                      # DISQUALIFY: no usable contact email (the operator's call)
    repo_rows.append({"first_seen":store.get(repo,{}).get("first_seen",""),
        "tier":g.get("tier",""),"score":g.get("self_solver_score",""),
        "region":bucket,"country":reg.get("country",""),"confidence":reg.get("confidence",""),
        "owner_repo":repo,"product":g.get("product_oneline",""),"email":em,
        "x_handle":g.get("x_handle",""),"live_app":g.get("live_app",""),"live_verified":g.get("live_verified",""),
        "evidence":g.get("evidence_snippet",""),"github_url":f"https://github.com/{repo.split('/')[0]}"})
repo_rows.sort(key=lambda r:(ORD.get(r["region"],9), 0 if r["confidence"]=="high" else 1, TB.get(r["tier"],9), -int(r["score"] or 0)))
rt=["status","contacted_on","channel","replied","notes"]
rd_cols=["first_seen","region","country","confidence","tier","score","owner_repo","product","email","x_handle","live_app","live_verified","github_url","evidence"]
radd,rtot=upsert(os.path.join(OUT,"worklist_repo.csv"),"owner_repo",repo_rows,rt,rd_cols)

# ---------------- SOCIAL worklist (review / comment / engage) ----------------
sstore=idx(rd("store/social_seen.csv"),"url")
linked=idx(rd("leads/social_linked.csv"),"url")
persona=idx(rd("leads/social_persona.csv"),"url")   # founder_builder | influencer_educator | unclear
warm=rd("leads/social_leads.csv")
soc_rows=[]
for w in warm:
    u=w.get("url",""); lk=linked.get(u,{})
    ident=lk.get("resolved_github") and ("github.com/"+lk["resolved_github"]) or lk.get("resolved_site","")
    soc_rows.append({"first_seen":sstore.get(u,{}).get("first_seen",""),
        "post_date":w.get("post_date",""),
        "persona":persona.get(u,{}).get("persona","unclear"),
        "warm_score":w.get("warm_score",""),"contactable":w.get("contactable",""),
        "classification":w.get("classification",""),"platform":w.get("platform",""),
        "author":w.get("author",""),"outreach_handle":w.get("outreach_handle",""),
        "resolved_identity":ident,"resolved_email":lk.get("resolved_email",""),
        "trigger":w.get("trigger",""),"destination":w.get("destination",""),
        "wedge_quote":w.get("wedge_quote",""),"post_url":u})
soc_rows.sort(key=lambda r:(0 if str(r["contactable"]).lower()=="true" else 1, -int(r["warm_score"] or 0)))
st=["status","engaged_on","action","replied","notes"]
sd_cols=["first_seen","post_date","persona","warm_score","contactable","classification","platform","author","outreach_handle","resolved_identity","resolved_email","trigger","destination","wedge_quote","post_url"]
# influencers/educators captured separately; NOT surfaced in the lead worklist (the operator's call)
infl=[r for r in soc_rows if r["persona"]=="influencer_educator"]
leadrows=[r for r in soc_rows if r["persona"]!="influencer_educator"]
sadd,stot=upsert(os.path.join(OUT,"worklist_social.csv"),"post_url",leadrows,st,sd_cols)
iadd,itot=upsert(os.path.join(ROOT,"leads","social_influencers.csv"),"post_url",infl,st,sd_cols)

print(f"REPO worklist:   +{radd} new  ->  {rtot} total  ({disq} disqualified: no usable email)  (outreach/worklist_repo.csv)")
print(f"SOCIAL worklist: +{sadd} new  ->  {stot} total  (outreach/worklist_social.csv)")
print(f"INFLUENCERS set aside: {itot} total  (leads/social_influencers.csv — captured, not in leads)")
print("tracking columns (status/notes/...) are preserved across runs; only new leads append.")

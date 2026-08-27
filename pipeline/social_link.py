#!/usr/bin/env python3
"""
social_link.py - identity resolution: turn a pseudonymous social warm-lead into a real
identity (site / github / email), and cross-link to a repo graduate in our store where possible.
Tries, per lead, strongest->weakest: github-in-post, product-domain-in-post, platform profile
(HN about, Bluesky handle-domain/profile, Mastodon profile), then corroborate via GitHub.
Usage: python3 pipeline/social_link.py
Out: leads/social_linked.csv  + a reliability summary to stdout.
"""
import csv, json, os, re, subprocess, time, urllib.parse, urllib.request, urllib.error
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
TOKEN=subprocess.check_output(["gh","auth","token"]).decode().strip()
GH={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","User-Agent":"else"}
def jget(url,headers=None,t=15):
    try: return json.load(urllib.request.urlopen(urllib.request.Request(url,headers=headers or {"User-Agent":"else"}),timeout=t))
    except Exception: return None
def gh_api(path):
    return jget(f"https://api.github.com/{path}",GH)
BADMAIL=("noreply","[bot]","bot@","@anthropic.com","@cursor.com","cursoragent","@example.com",
         ".local",".home",".lan","users.noreply","github-actions","actions@","action@","@github.com",
         "privaterelay","@sqi.app")
def best_email(owner, repo=None):
    """A usable contact email for a resolved GitHub identity: profile email, then commit email
    from the named repo, then from the owner's recent repos."""
    prof=gh_api(f"users/{owner}") or {}
    pe=(prof.get("email") or "").strip()
    if pe and not any(b in pe.lower() for b in BADMAIL): return pe
    repos=[repo] if repo else []
    if not repos:
        rl=gh_api(f"users/{owner}/repos?sort=pushed&per_page=5")
        if isinstance(rl,list): repos=[r["name"] for r in rl][:3]
    for rp in repos:
        cm=gh_api(f"repos/{owner}/{rp}/commits?per_page=30")
        if isinstance(cm,list):
            for c in cm:
                e=((c.get("commit") or {}).get("author") or {}).get("email","").lower().strip()
                if e and not any(b in e for b in BADMAIL): return e
    return ""

URLRE=re.compile(r"https?://[^\s)\"'<>]+")
GHRE=re.compile(r"github\.com/([A-Za-z0-9-]+)(?:/([A-Za-z0-9._-]+))?",re.I)
BAREDOM=re.compile(r"\b([a-z0-9][a-z0-9-]{1,}\.(?:com|io|dev|app|ai|one|co|net|org|so|me|xyz|tech|build|page|site))\b",re.I)
def sites_from_bio(text):
    out=[dom(u) for u in URLRE.findall(text)]
    out+=[m.group(1).lower() for m in BAREDOM.finditer(text)]
    return [d for d in out if d and not any(s in d for s in PLAT)]
PLAT=("reddit.com","lovable.dev","supabase","youtube","youtu.be","twitter.com","x.com",
      "news.ycombinator","bsky.app","mastodon","imgur","google.","apps.apple","play.google",
      "vercel.com","netlify.com","cloudflare","stripe.com","discord","medium.com","substack")
def dom(u):
    u=re.sub(r"^https?://","",u.strip().lower()).lstrip("www.")
    return re.split(r"[/?#\s]",u)[0].strip(".,)")

# ---- repo store domain/owner index (for cross-stream matching) ----
repo_domain={}; repo_owner={}; repo_meta={}
def load_repo(path, lacol, ownercol="owner_repo", emailcols=("best_contact","emails")):
    if not os.path.exists(path): return
    for r in csv.DictReader(open(path)):
        repo=r.get(ownercol,"")
        if not repo: continue
        la=r.get(lacol,"")
        if la: repo_domain[dom(la)]=repo
        repo_owner[repo.split("/")[0].lower()]=repo
        em=next((r.get(c) for c in emailcols if r.get(c)), "")
        repo_meta.setdefault(repo,{}).update({"email":em or repo_meta.get(repo,{}).get("email","")})
for p,la in [("leads/graduates_full.csv","live_app"),("data/candidates_full.csv","live_app"),
             ("data/candidates_repo.csv","live_app"),("data/candidates_lastweek.csv","live_app")]:
    load_repo(os.path.join(ROOT,p), la)
for p in ["leads/region_tagged_full.csv","leads/earlier-runs/region_tagged.csv"]:
    fp=os.path.join(ROOT,p)
    if os.path.exists(fp):
        for r in csv.DictReader(open(fp)):
            repo=r.get("owner_repo","")
            if repo: repo_meta.setdefault(repo,{}).update({"country":r.get("country",""),"email":repo_meta.get(repo,{}).get("email") or r.get("best_contact","")})

def store_match(domain=None, owner=None):
    if domain and dom(domain) in repo_domain: return repo_domain[dom(domain)]
    if owner and owner.lower() in repo_owner: return repo_owner[owner.lower()]
    return ""

# ---- resolution ----
import glob
_raws=sorted(glob.glob(os.path.join(ROOT,"data/social_raw_*.json")))
raw={r["url"]:r for r in json.load(open(_raws[-1]))} if _raws else {}
warm=list(csv.DictReader(open(os.path.join(ROOT,"leads/social_leads.csv"))))

def resolve(w):
    plat=w["platform"]; author=w["author"]; r=raw.get(w["url"],{})
    text=(r.get("title","")+" "+r.get("text",""))
    out={"resolved_via":"","resolved_github":"","resolved_site":"","resolved_email":"",
         "store_match":"","confidence":"low"}
    # 1) github in post
    m=GHRE.search(text)
    if m:
        owner=m.group(1); repo=(m.group(2) or "").strip(".,)")
        u=gh_api(f"users/{owner}")
        if u and u.get("type")=="User":
            out.update({"resolved_via":"github-in-post","resolved_github":owner,
                        "resolved_site":u.get("blog") or "","resolved_email":best_email(owner, repo or None),"confidence":"high"})
            out["store_match"]=store_match(owner=owner) or store_match(domain=u.get("blog"))
            return out
    # 2) product domain in post
    for u in URLRE.findall(text):
        d=dom(u)
        if d and not any(s in d for s in PLAT):
            out.update({"resolved_via":"domain-in-post","resolved_site":d,"confidence":"medium"})
            out["store_match"]=store_match(domain=d)
            if out["store_match"]: out["confidence"]="high"
            return out
    # 3) platform profile
    if plat=="hackernews":
        prof=jget(f"https://hacker-news.firebaseio.com/v0/user/{author}.json")
        about=(prof or {}).get("about","") if prof else ""
        gh_m=GHRE.search(about); sites=sites_from_bio(about)
        if gh_m:
            out.update({"resolved_via":"hn-about-github","resolved_github":gh_m.group(1),"confidence":"high"})
            out["store_match"]=store_match(owner=gh_m.group(1)); return out
        if sites:
            out.update({"resolved_via":"hn-about-site","resolved_site":sites[0],"confidence":"medium"})
            out["store_match"]=store_match(domain=sites[0])
            if out["store_match"]: out["confidence"]="high"
            return out
    if plat=="bluesky":
        # bluesky handle is often a custom domain (their site)
        if "." in author and not author.endswith(".bsky.social"):
            out.update({"resolved_via":"bsky-handle-domain","resolved_site":author,"confidence":"medium"})
            out["store_match"]=store_match(domain=author); return out
        prof=jget(f"https://api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={urllib.parse.quote(author)}")
        desc=(prof or {}).get("description","") if prof else ""
        gh_m=GHRE.search(desc); sites=sites_from_bio(desc)
        if gh_m: out.update({"resolved_via":"bsky-bio-github","resolved_github":gh_m.group(1),"confidence":"high","store_match":store_match(owner=gh_m.group(1))}); return out
        if sites: out.update({"resolved_via":"bsky-bio-site","resolved_site":sites[0],"confidence":"medium","store_match":store_match(domain=sites[0])}); return out
    if plat=="mastodon" and "@" in author:
        inst=author.split("@")[-1]
        if "." in inst:  # the handle's home instance/domain is a weak identity anchor
            out.update({"resolved_via":"mastodon-instance","resolved_site":inst,"confidence":"low"})
            return out
    # 4) handle-as-github corroborated by a Lovable repo (collision-safe)
    cand=author.split("/")[-1].split("@")[0].split(".")[0]
    if cand and len(cand)>2:
        u=gh_api(f"users/{cand}")
        if u and u.get("type")=="User":
            cnt=gh_api(f"search/repositories?q=user:{cand}+%22lovable.dev/projects%22+in:readme")
            if cnt and cnt.get("total_count",0)>0:
                out.update({"resolved_via":"handle=github+lovable","resolved_github":cand,
                            "resolved_site":u.get("blog") or "","resolved_email":best_email(cand),"confidence":"high"})
                out["store_match"]=store_match(owner=cand); return out
    return out

rows=[]
for w in warm:
    res=resolve(w); time.sleep(0.3)
    sm=res["store_match"]
    rows.append({"warm_score":w["warm_score"],"platform":w["platform"],"classification":w["classification"],
        "author":w["author"],"resolved_via":res["resolved_via"],"resolved_github":res["resolved_github"],
        "resolved_site":res["resolved_site"],"resolved_email":res["resolved_email"] or (repo_meta.get(sm,{}).get("email","") if sm else ""),
        "store_match_repo":sm,"inherited_country":repo_meta.get(sm,{}).get("country","") if sm else "",
        "confidence":res["confidence"],"url":w["url"]})

cols=["warm_score","confidence","platform","classification","author","resolved_via","resolved_github","resolved_site","resolved_email","store_match_repo","inherited_country","url"]
with open(os.path.join(ROOT,"leads/social_linked.csv"),"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)

from collections import Counter
resolved=[r for r in rows if r["resolved_via"]]
ident=[r for r in rows if r["resolved_github"] or r["resolved_site"] or r["resolved_email"]]
sm=[r for r in rows if r["store_match_repo"]]
print(f"=== identity resolution on {len(rows)} warm social leads ===")
print(f"resolved to SOME real identity (github/site/email): {len(ident)}/{len(rows)}")
print(f"cross-linked to a repo in our store: {len(sm)}")
print("by method:", dict(Counter(r["resolved_via"] or "unresolved" for r in rows)))
print("by platform (resolved):", dict(Counter(r["platform"] for r in ident)))
print("confidence (resolved):", dict(Counter(r["confidence"] for r in ident)))
print("\n-- resolved leads --")
for r in ident:
    print(f"  {r['confidence']:6} {r['platform']:10} {r['author'][:20]:20} via {r['resolved_via']:22} -> gh:{r['resolved_github'] or '-'} site:{r['resolved_site'] or '-'} store:{r['store_match_repo'] or '-'}")
print("wrote leads/social_linked.csv")

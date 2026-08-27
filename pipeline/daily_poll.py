#!/usr/bin/env python3
"""
daily_poll.py - incremental daily pull of Lovable graduates (the deterministic, cron-safe half).

Pulls repos pushed since {--since} (default: yesterday), runs the identification gates
(origin UUID + graduation marker + web-only + contacts), dedups against the store, and
writes ONLY the net-new candidates. The LLM judgment + region passes run on that small file.

Run:   python3 pipeline/daily_poll.py [--since YYYY-MM-DD] [--pages N]
Out:   data/candidates_daily_{rundate}.csv
       data/candidates_daily_{rundate}.netnew.csv          (net-new only - feed to judgment)
       data/candidates_daily_{rundate}_netnew_enriched.json (evidence for judgment)
Auth:  uses `gh auth token`.
"""
import argparse, csv, json, os, re, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0, HERE); import dedup_store
DATA=os.path.join(ROOT,"data"); os.makedirs(DATA, exist_ok=True)

TOKEN=subprocess.check_output(["gh","auth","token"]).decode().strip()
H={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","User-Agent":"else-daily"}
MARKERS={"CLAUDE.md":"ClaudeCode",".claude":"ClaudeCode",".cursorrules":"Cursor",".cursor":"Cursor",".cursorignore":"Cursor","AGENTS.md":"Codex",".codex":"Codex"}
SKIP=("tutorial","clone","example","template","boilerplate","starter","lovable-to-","awesome","-list","playground","demo-repo")
BADMAIL=("noreply","[bot]","bot@","@anthropic.com","@cursor.com","cursoragent","@example.com",".local",".home",".lan","users.noreply","github-actions","actions@","action@","@github.com","privaterelay.appleid.com","@sqi.app")
NATIVE={"react-native","expo","@expo/cli","nativescript","@react-native-community/cli"}
WRAP=("@capacitor/","@ionic/")
UUID=re.compile(r"lovable\.dev/projects/[0-9a-f]{8}-[0-9a-f]{4}",re.I)

def api(u,tries=4):
    for _ in range(tries):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers=H),timeout=25))
        except urllib.error.HTTPError as e:
            if e.code in (403,429): time.sleep(8); continue
            return None
        except Exception: return None
    return None
def raw(full,p):
    try: return urllib.request.urlopen(urllib.request.Request(f"https://raw.githubusercontent.com/{full}/HEAD/{p}",headers={"User-Agent":"else"}),timeout=15).read().decode("utf-8","ignore")
    except: return ""

def run(since, pages):
    rundate=date.today().isoformat()
    q=urllib.parse.quote(f'"lovable.dev/projects" in:readme pushed:>{since}')
    pool={}
    for page in range(1,pages+1):
        res=api(f"https://api.github.com/search/repositories?q={q}&sort=updated&order=desc&per_page=100&page={page}")
        items=(res or {}).get("items",[])
        if not items: break
        for it in items:
            full=it["full_name"]; blob=(full+" "+(it.get("description") or "")).lower()
            if it.get("fork") or any(s in blob for s in SKIP): continue
            pool[full]=it
        time.sleep(1)
    print(f"[daily {rundate}] since {since}: population {len(pool)}",flush=True)

    def mark(it):
        c=api(f"https://api.github.com/repos/{it['full_name']}/contents")
        if not isinstance(c,list): return None
        hits=[m for m in MARKERS if m in {x['name'] for x in c}]
        return (it,hits) if hits else None
    marked=[r for r in (lambda: list(ThreadPoolExecutor(max_workers=6).map(mark,pool.values())))() if r]
    print(f"[daily {rundate}] marker-positive {len(marked)}",flush=True)

    rows=[]; enr={}; native=0
    for it,hits in marked:
        full=it["full_name"]; readme=raw(full,"README.md") or raw(full,"readme.md")
        if not UUID.search(readme or ""): continue
        owner=it["owner"]["login"]; pj=raw(full,"package.json"); deps=set()
        try:
            d=json.loads(pj); deps=set({**d.get("dependencies",{}),**d.get("devDependencies",{})}.keys())
        except: pass
        if deps & NATIVE or raw(full,"pubspec.yaml"): native+=1; continue
        plat="web_mobile_wrapper" if any(x.startswith(WRAP) for x in deps) else "web"
        cm=api(f"https://api.github.com/repos/{full}/commits?per_page=100"); emails=[]
        if isinstance(cm,list):
            for x in cm:
                e=((x.get("commit") or {}).get("author") or {}).get("email","").lower().strip()
                if e and not any(b in e for b in BADMAIL) and e not in emails: emails.append(e)
        emails.sort(key=lambda e:0 if owner.lower().split("-")[0] in e else 1)
        prof=api(f"https://api.github.com/users/{owner}") or {}
        dest=sorted({MARKERS[m] for m in hits})
        rows.append({"owner_repo":full,"owner":owner,"markers":"/".join(hits),"destination":",".join(dest),
            "platform":plat,"stars":it.get("stargazers_count",0),"last_push":(it.get("pushed_at") or "")[:10],
            "live_app":it.get("homepage") or "","emails":";".join(emails[:3]),"x_handle":prof.get("twitter_username") or "",
            "blog":prof.get("blog") or "","location":prof.get("location") or "",
            "description":(it.get("description") or "").replace("\n"," ")[:160]})
        mk={}
        for fn in ("CLAUDE.md",".cursorrules","AGENTS.md"):
            if fn in "/".join(hits):
                t=raw(full,fn)
                if t: mk[fn]=t[:2200]
        enr[full]={**rows[-1],"readme":readme[:3500],"marker_files":mk,
            "commit_subjects":[ (c.get("commit") or {}).get("message","").split("\n")[0][:90] for c in (cm if isinstance(cm,list) else []) ][:20]}

    if not rows:
        print(f"[daily {rundate}] 0 verified candidates - nothing to merge"); return
    csv_path=os.path.join(DATA,f"candidates_daily_{rundate}.csv")
    with open(csv_path,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"[daily {rundate}] verified web-only {len(rows)} (native dropped {native})",flush=True)

    total,new,netnew_path = dedup_store.merge_csv(csv_path, f"daily-{rundate}", rundate)
    print(f"[daily {rundate}] NET-NEW (not seen before): {new}/{total}",flush=True)
    if netnew_path:
        newrepos={r["owner_repo"] for r in csv.DictReader(open(netnew_path))}
        json.dump([enr[r] for r in newrepos if r in enr],
                  open(os.path.join(DATA,f"candidates_daily_{rundate}_netnew_enriched.json"),"w"))
        print(f"[daily {rundate}] wrote {os.path.relpath(netnew_path,ROOT)} (+ enriched) -> run judgment/region on this",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--since", default=(date.today()-timedelta(days=1)).isoformat())
    ap.add_argument("--pages", type=int, default=10)
    a=ap.parse_args(); run(a.since, a.pages)

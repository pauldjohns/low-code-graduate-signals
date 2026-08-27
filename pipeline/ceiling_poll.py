#!/usr/bin/env python3
"""
ceiling_poll.py - incremental poll of PRE-GRADUATION Lovable builders ("ceiling-pushers").

The inverse of daily_poll.py's marker gate: finds Lovable-origin repos with NO pro-tool
marker that show readiness signals, BEFORE they graduate to Claude Code/Cursor/Codex.
Method + evidence: method/CEILING.md. Deterministic and cron-safe (no LLM pass needed).

Segments (assigned from commit classes; see CEILING.md):
  A_hybrid       Lovable bot alive <=30d AND >=3 local-git human commits in 30d
  B_all_bot      >=95% bot, bot commit <=7d, true age >=60d, seriousness evidence
  C_mover        human-led now; bot went silent 0-45d ago (or fresh repo w/ migrated history)
  shadow_graduate / presumed_silent_graduate / already_tooled_other  -> routed out, kept in store

Run:   python3 pipeline/ceiling_poll.py [--since YYYY-MM-DD] [--dry-run] [--no-contact] [--scrape-sites]
       python3 pipeline/ceiling_poll.py --stats
Out:   data/ceiling_daily_{rundate}.csv          (every pulled repo, gate-tagged; prunable)
       store/ceiling_seen.csv                    (dedup store, keyed by repo_id; never delete)
       outreach/worklist_ceiling.csv             (append-only upsert; tracking cols preserved)
Auth:  uses `gh auth token`.
"""
import argparse, csv, json, os, random, re, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
DATA=os.path.join(ROOT,"data"); os.makedirs(DATA, exist_ok=True)
STORE=os.path.join(ROOT,"store","ceiling_seen.csv")
WORKLIST=os.path.join(ROOT,"outreach","worklist_ceiling.csv")
STORE_FIELDS=["repo_id","owner_repo","owner","first_seen","last_seen","times_seen","segment",
              "bot_total","bot_share","trend","best_contact","status","graduated_on","source_runs"]
TRACK_COLS=["status","contacted_on","channel","replied","notes"]
# verify_* are written by pipeline/verify_queue.py (Bouncer mailbox check), never by this
# script. They MUST stay in DATA_COLS: worklist_upsert/worklist_retire rewrite the file with
# fieldnames=TRACK_COLS+DATA_COLS and extrasaction="ignore", so any column missing from this list
# is silently dropped on the next poll. That is exactly how `email_check` kept vanishing in the
# Review repo. Regression-tested in pipeline/test_worklist_columns.py.
DATA_COLS=["first_seen","segment","trend","bot_total","true_age_days","c30_bot","c30_local","last_bot",
           "reverts_in_100","signal_deps","product","live_url","email","x_handle","contact_paths",
           "owner_location","owner_repo","github_url",
           "verify_status","verify_reason","verify_date"]

H={"Accept":"application/vnd.github+json","User-Agent":"else-ceiling"}   # token added lazily (so --stats needs no gh auth)
def _auth():
    H["Authorization"]="Bearer "+subprocess.check_output(["gh","auth","token"]).decode().strip()

# --- per-phase API call counter (observability for the shared gh-token budget; printed at run end) ---
# api()/raw() bump this; run() sets _CALL_PHASE at each phase boundary. Never allowed to break a call.
_CALL_LOCK=threading.Lock(); CALL_COUNTS={}; _CALL_PHASE=["init"]
def _tally(i):   # i=0 core api(), i=1 raw()
    try:
        with _CALL_LOCK: CALL_COUNTS.setdefault(_CALL_PHASE[0],[0,0])[i]+=1
    except Exception: pass

# --- identity + gate constants (mirror daily_poll.py where shared) ---
MARKERS={"CLAUDE.md":"ClaudeCode",".claude":"ClaudeCode",".cursorrules":"Cursor",".cursor":"Cursor",
         ".cursorignore":"Cursor","AGENTS.md":"Codex",".codex":"Codex"}
OTHER_TOOL={".windsurfrules",".windsurf",".clinerules","GEMINI.md",".gemini",".aider.conf.yml"}
SKIP=("tutorial","clone","example","template","boilerplate","starter","lovable-to-","awesome","-list","playground","demo-repo")
BADMAIL=("noreply","[bot]","bot@","@anthropic.com","@cursor.com","cursoragent","@example.com",".local",".home",
         ".lan","users.noreply","github-actions","actions@","action@","@github.com","privaterelay.appleid.com","@sqi.app")
NATIVE={"react-native","expo","@expo/cli","nativescript","@react-native-community/cli"}
WRAP=("@capacitor/","@ionic/")
UUID=re.compile(r"lovable\.dev/projects/[0-9a-f]{8}-[0-9a-f]{4}",re.I)
# no_uuid origin-proof softening: admit search hits whose README lacks the editor-UUID link when a
# build-level Lovable-origin proof exists (lovable-tagger dep / gpteng CDN). Kill-switch: set False to
# fall back to UUID-only origin verification in a live checkout without a revert (mirrors the config
# dry_run overlay pattern). Default True = enabled once merged. See lovable_origin() + method/NOUUID-GATE-SPEC.md.
ORIGIN_PROOF=True
BOT_LOGINS={"lovable-dev[bot]","gpt-engineer-app[bot]"}
LOVABLE_EMAILS=("gpt-engineer-app[bot]","noreply@lovable.dev")
INFRA_BOT_LOGINS={"github-actions[bot]","dependabot[bot]","renovate[bot]","actions-user","web-flow"}
INFRA_EMAILS=("action@github.com","actions@github.com")
BOTMAIL_RE=re.compile(r"\d+\+.*\[bot\]@users\.noreply\.github\.com")
AGENT_HINTS=("co-authored-by: claude","co-authored-by: cursor","co-authored-by: codex","generated with claude",
             "generated with [claude","cursoragent","devin-ai","aider:","co-authored-by: aider","openhands")
SIGNAL_DEPS=("stripe","@stripe/","@paddle/","lemonsqueezy","razorpay","resend","@sendgrid/","twilio","nodemailer",
             "postmark","posthog","@sentry/","mixpanel","@amplitude/","@clerk/","next-auth","@auth0/","openai",
             "@anthropic-ai/","@google/generative-ai","groq-sdk","replicate","express","hono","fastify","prisma",
             "drizzle-orm","@trpc/")
ARTIFACT_NAMES={".github":"ci","docs":"docs","Dockerfile":"deploy","vercel.json":"deploy","netlify.toml":"deploy",
                "fly.toml":"deploy","render.yaml":"deploy","vitest.config.ts":"tests","vitest.config.js":"tests",
                "playwright.config.ts":"tests","cypress.config.ts":"tests","jest.config.js":"tests",
                "server":"own_backend","api":"own_backend","backend":"own_backend","supabase":"supabase"}
URL_RE=re.compile(r"https?://[^\s)\"'<>\]]+")
SKIP_URL=("github.com","githubusercontent","lovable.dev","lovableproject.com","shields.io","img.shields",
          "docs.lovable","vitejs","react.dev","tailwindcss","typescriptlang","supabase.com","npmjs.com",
          "developer.mozilla","ui.shadcn","badge","opengraph","cdn.gpteng.co","youtube.com","youtu.be","localhost")
TREND_RANK={"increasing":0,"new_activity":1,"steady":2,"dropping":3,"sparse":4,"":5,"err":5,None:5}
SEG_RANK={"B_all_bot":0,"A_hybrid":1,"C_mover":2,"C_mover_fresh":2}

# --- edge-function API integration (a seriousness signal package.json can't see) ---
# Lovable proxies third-party APIs (Stripe/Finnhub/Resend/OpenAI...) through Supabase edge
# functions with dashboard-managed secrets, so a serious integration leaves NO package.json dep.
# Detect it from the function bodies: a fetch to a non-Lovable external host, or a curated
# vendor secret. Lovable's own one-click AI gateway (ai.gateway.lovable.dev / LOVABLE_API_KEY)
# does NOT count. If Lovable renames its gateway host/env var, re-verify EDGE_EXCLUDE_HOST below.
EDGE_EXCLUDE_HOST=("supabase.co","supabase.in","supabase.com","lovable.dev","lovable.app","lovableproject.com",
    "gpteng","gpt-engineer","localhost","127.0.0.1","0.0.0.0","deno.land","esm.sh","jsr.io","deno.dev",
    "githubusercontent","github.com","fonts.googleapis","fonts.gstatic","gstatic.com","google-analytics",
    "googletagmanager","doubleclick","unpkg.com","jsdelivr","cdnjs","cloudflare","w3.org","schema.org",
    "example.com","example.org","placehold","unsplash","picsum","gravatar","imgur","youtube","youtu.be",
    "vimeo","facebook.com","fbcdn","linkedin.com","x.com","twitter.com",".local","vercel.app","netlify.app","pages.dev")
EDGE_VENDOR_SECRET=re.compile(r"(STRIPE|RESEND|SENDGRID|POSTMARK|MAILGUN|TWILIO|OPENAI|ANTHROPIC|GEMINI|GOOGLE_AI|"
    r"GROQ|REPLICATE|MISTRAL|COHERE|PERPLEXITY|DEEPGRAM|ELEVENLABS|HUGGINGFACE|FINNHUB|ALPHAVANTAGE|POLYGON|"
    r"FIRECRAWL|SERPAPI|RAPIDAPI|MAPBOX|GOOGLE_MAPS|GOOGLE_SHEETS|GOOGLE_MAIL|OPENWEATHER|CLERK|AUTH0|PLAID|"
    r"PADDLE|LEMON|RAZORPAY|PAYSTACK|NOTION|AIRTABLE|ALGOLIA|PINECONE|SENDBIRD|AGORA|LIVEKIT)",re.I)
EDGE_FETCH_RE=re.compile(r"""(?:fetch|axios(?:\.\w+)?|\.get|\.post|\.put|\.patch)\s*\(\s*[`'"]\s*(https?://[^`'"\s)]+)""",re.I)
EDGE_DENOENV_RE=re.compile(r"""Deno\.env\.get\(\s*['"]([A-Za-z0-9_]+)""")

def _edge_host(u):
    m=re.match(r"https?://([^/`'\"?:]+)",u or ""); return (m.group(1).lower() if m else "")

def edge_api_integration(full,cap_files=6):
    """(hosts,secrets) of NON-Lovable third-party integrations in a repo's Supabase edge functions.
    Returns empties on ANY error / no functions / Lovable-gateway-only. MUST NEVER raise: it runs
    inside the unguarded score() thread pool, where a raise would kill a whole hourly scoring pass.
    A truncated tree can hide functions -> fail-closed (consistent with the B_age_unknown policy)."""
    try:
        tree=api(f"https://api.github.com/repos/{full}/git/trees/HEAD?recursive=1")
        if not isinstance(tree,dict): return set(),set()
        fns=[p for t in tree.get("tree",[]) if isinstance(t,dict) and t.get("type")=="blob"
             for p in [str(t.get("path",""))] if p.startswith("supabase/functions/") and p.endswith((".ts",".js"))]
        hosts=set(); secrets=set()
        for fn in fns[:cap_files]:
            try: body=raw(full,fn) or ""
            except Exception: body=""
            if not body: continue
            try:
                for m in EDGE_FETCH_RE.finditer(body):
                    h=_edge_host(m.group(1))
                    if h and not any(x in h for x in EDGE_EXCLUDE_HOST): hosts.add(h)
                for m in EDGE_DENOENV_RE.finditer(body):
                    name=m.group(1)
                    if "SUPABASE" in name.upper() or name.upper()=="LOVABLE_API_KEY": continue
                    if EDGE_VENDOR_SECRET.search(name): secrets.add(name)
            except Exception: continue
        return hosts,secrets
    except Exception:
        return set(),set()

def assign_segment(agent,last_bot_age,c30_local,bot,substant,created_age,share,seriousness,edge_detect):
    """Pure segment cascade (extracted from score() for testability). edge_detect is a zero-arg
    callable returning truthy iff a non-Lovable edge-function integration exists; it is invoked
    LAZILY only in the B branch (after agent/A/C/PSG are excluded), so no other segment can trigger
    an API call or flip. Returns (segment, edge_marker_or_None)."""
    if agent: return "shadow_graduate",None
    if last_bot_age<=30 and c30_local>=3: return "A_hybrid",None
    if c30_local>=2 and 0<=last_bot_age<=45 and bot>0: return ("C_mover" if substant else "C_thin"),None
    if c30_local>=2 and created_age<30 and 0<bot<=5: return ("C_mover_fresh" if substant else "C_thin"),None  # bot>0: a fresh repo w/ NO Lovable-bot history is a hand-pushed clone, not a migrated Lovable project
    if c30_local>=2 and last_bot_age>45: return "presumed_silent_graduate",None
    if share>=0.95 and last_bot_age<=7:
        if seriousness: return "B_all_bot",None
        edge=edge_detect()                       # lazy: only all-bot+recent+no-pkg-signal repos pay the probe
        if edge: return "B_all_bot",edge
        return "B_light",None
    return "other",None

# ---------------- HTTP helpers ----------------
def api(url,tries=4,want_resp=False):
    _tally(0)
    for i in range(tries):
        try:
            resp=urllib.request.urlopen(urllib.request.Request(url,headers=H),timeout=25)
            body=json.load(resp)                      # read BEFORE any rate sleep (buffer won't survive it)
            rem=int(resp.headers.get("X-RateLimit-Remaining","999"))
            if resp.headers.get("X-RateLimit-Resource","core")=="core" and rem<80:
                wait=max(0,int(resp.headers.get("X-RateLimit-Reset",time.time()+60))-time.time())+5
                print(f"[rate] core low ({rem}), sleeping {int(wait)}s",flush=True); time.sleep(wait)
            return (resp,body) if want_resp else body
        except urllib.error.HTTPError as e:
            if e.code in (403,429): time.sleep(20*(i+1)); continue
            return (None,None) if want_resp else None
        except Exception: time.sleep(3)
    return (None,None) if want_resp else None

def raw(full,p,tries=2):
    """'' = fetched-and-missing (404). None = transient error (429/network) — caller can tag fetch_err."""
    _tally(1)
    for i in range(tries):
        try:
            time.sleep(random.uniform(0.25,0.45))
            r=urllib.request.urlopen(urllib.request.Request(
                f"https://raw.githubusercontent.com/{full}/HEAD/{p}",headers={"User-Agent":"else-ceiling"}),timeout=15)
            return r.read().decode("utf-8","ignore")
        except urllib.error.HTTPError as e:
            if e.code==429: print("[rate] raw 429, sleeping 30s",flush=True); time.sleep(30); continue
            return "" if e.code==404 else None
        except Exception:
            time.sleep(2)
    return None

def link_count(full,since=None,until=None):
    q="per_page=1"+(f"&since={since}T00:00:00Z" if since else "")+(f"&until={until}T00:00:00Z" if until else "")
    resp,body=api(f"https://api.github.com/repos/{full}/commits?{q}",want_resp=True)
    if resp is None: return None
    m=re.search(r'[?&]page=(\d+)>; rel="last"',resp.headers.get("Link",""))
    return int(m.group(1)) if m else (len(body) if isinstance(body,list) else 0)

# ---------------- commit classification ----------------
def classify_commit(c):
    a=c.get("author") or {}; ca=(c.get("commit") or {}).get("author") or {}; cm=(c.get("commit") or {}).get("committer") or {}
    email=(ca.get("email") or "").lower(); name=ca.get("name") or ""
    msg=((c.get("commit") or {}).get("message") or "").lower()
    agent=any(h in msg for h in AGENT_HINTS) or "cursoragent" in email
    if a.get("login") in BOT_LOGINS or any(x in email for x in LOVABLE_EMAILS) or name=="gpt-engineer-app[bot]":
        return "lovable_bot",False
    if (a.get("type")=="Bot" or a.get("login") in INFRA_BOT_LOGINS or email in INFRA_EMAILS
            or name.endswith("[bot]") or BOTMAIL_RE.search(email)):
        return "infra_bot",agent
    if cm.get("name")=="GitHub" and (cm.get("email") or "").lower()=="noreply@github.com":
        return "web_flow",agent
    return "local_git",agent

# ---------------- store ----------------
def store_load():
    if not os.path.exists(STORE): return {}
    return {r["repo_id"]:r for r in csv.DictReader(open(STORE))}
def store_save(st):
    os.makedirs(os.path.dirname(STORE),exist_ok=True)
    with open(STORE,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=STORE_FIELDS); w.writeheader()
        for k in sorted(st,key=lambda x:int(x)): w.writerow({c:st[k].get(c,"") for c in STORE_FIELDS})
def store_stats():
    st=store_load(); from collections import Counter
    print(f"ceiling store: {len(st)} repos ({os.path.relpath(STORE,ROOT)})")
    print(" segment:",dict(Counter(v.get("segment") or "?" for v in st.values())))
    print(" graduated:",sum(1 for v in st.values() if v.get("graduated_on")))

# ---------------- worklist upsert ----------------
# Same tracking contract as build_worklist.py (TRACK_COLS survive every run, DATA_COLS refresh)
# but UNLIKE build_worklist this re-sorts the file each run — segment/trend rank IS the outreach
# order and the dashboard reads file order. Stable tiebreak keeps diffs quiet.
def _i(v):
    try: return int(v)
    except Exception: return 0   # hand-edited/blank values must not crash the sort
def worklist_upsert(newrows):
    existing={r["owner_repo"]:r for r in (list(csv.DictReader(open(WORKLIST))) if os.path.exists(WORKLIST) else [])}
    added=0
    for nr in newrows:
        k=nr.get("owner_repo","")
        if not k: continue
        if k in existing:
            for dc in DATA_COLS:
                if dc in nr and dc!="first_seen": existing[k][dc]=nr[dc]   # first_seen never refreshed
        else:
            existing[k]={**{t:"" for t in TRACK_COLS},**nr}; added+=1
    rows=sorted(existing.values(),key=lambda r:(SEG_RANK.get(r.get("segment"),9),
        TREND_RANK.get(r.get("trend"),5),-_i(r.get("bot_total")),r.get("owner_repo","")))
    os.makedirs(os.path.dirname(WORKLIST),exist_ok=True)
    with open(WORKLIST+".tmp","w",newline="") as f:      # atomic: never let a reader see a torn write
        w=csv.DictWriter(f,fieldnames=TRACK_COLS+DATA_COLS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    os.replace(WORKLIST+".tmp",WORKLIST)
    return added,len(rows)

def worklist_retire(owner_repos,status="graduated"):
    """Mark converted rows (marker appeared) so a graduated builder never gets the pre-grad pitch.
    Only fills a BLANK status — the operator's own tracking edits are never overwritten."""
    if not owner_repos or not os.path.exists(WORKLIST): return 0
    rows=list(csv.DictReader(open(WORKLIST))); n=0
    for r in rows:
        if r.get("owner_repo") in owner_repos and not r.get("status"):
            r["status"]=status; n+=1
    if n:
        with open(WORKLIST+".tmp","w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=TRACK_COLS+DATA_COLS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
        os.replace(WORKLIST+".tmp",WORKLIST)
    return n

# ---------------- phases ----------------
def pull(since,rundate):
    """Day-sliced search -> deduped population (id-keyed)."""
    _CALL_PHASE[0]="pull"
    pool={}
    d=datetime.strptime(since,"%Y-%m-%d").date()
    days=[]
    while d<=date.today(): days.append(d.isoformat()); d+=timedelta(days=1)
    base='"lovable.dev/projects" in:readme fork:false'
    def q_items(qs):
        got=[]
        for page in range(1,11):
            time.sleep(2.2)
            res=api(f"https://api.github.com/search/repositories?q={urllib.parse.quote(qs)}&sort=updated&order=desc&per_page=100&page={page}")
            items=(res or {}).get("items",[])
            got.extend(items)
            if len(items)<100: break
        return got
    for day in days:
        probe=api(f"https://api.github.com/search/repositories?q={urllib.parse.quote(base+f' pushed:{day}')}&per_page=1")
        total=(probe or {}).get("total_count",0); time.sleep(2.2)
        items=q_items(base+f" pushed:{day}") if total<=950 else \
              [x for h1,h2 in ((0,5),(6,11),(12,17),(18,23)) for x in q_items(base+f" pushed:{day}T{h1:02d}:00:00..{day}T{h2:02d}:59:59")]
        for it in items: pool[it["id"]]=it
        print(f"[pull] {day}: {total} -> pool {len(pool)}",flush=True)
    return pool

def lovable_origin(txt,full):
    """Lovable-origin proof for a search hit whose README already mentions lovable.dev/projects.
    Returns (origin, pkg_text):
      origin: "uuid"   README carries the full editor-UUID link (today's strict proof)
              "tagger" package.json deps/devDeps carry lovable-tagger (Lovable's Vite plugin; definitive)
              "gpteng" index.html carries the gpteng engine CDN (scoped to the repo's OWN index.html)
              ""       definitively no proof (all fetches resolved, none matched)  -> caller: no_uuid
              None     transient fetch error (undetermined)                        -> caller: proof_err
      pkg_text: the fetched package.json string (so g_pkg can reuse it, no double fetch) or None.
    MUST NEVER raise: runs inside the g_readme thread pool, where a raise kills the whole run (same
    discipline as edge_api_integration). Parse-based tagger check (a 'remove lovable-tagger' tutorial
    carries the literal string but not the dep). raw(): ""=404, None=transient."""
    if UUID.search(txt): return "uuid",None
    try:
        transient=False
        pj=raw(full,"package.json")
        if pj is None: transient=True
        else:
            try:
                d=json.loads(pj or "")
                if isinstance(d,dict) and "lovable-tagger" in {**d.get("dependencies",{}),**d.get("devDependencies",{})}:
                    return "tagger",pj
            except Exception: pass                 # malformed/odd package.json -> not a tagger proof, keep looking
        html=raw(full,"index.html")
        if html is None: transient=True
        elif "gpteng" in html: return "gpteng",pj  # cdn.gpteng.co / gpteng.co, injected into the built app
        return (None,pj) if transient else ("",pj)
    except Exception:
        return None,None                           # undetermined -> proof_err (re-enters next run)

def cap_netnew(netnew,cap):
    """Flatten the first-run scoring burst: score the repos nearest to aging out of the --since window
    first (pushed_at ASC), defer the rest. Deferred repos are un-stored, so they re-appear and score on
    a later run. Returns (kept, deferred_count)."""
    ordered=sorted(netnew,key=lambda r:r.get("pushed_at",""))
    return (ordered[:cap], max(0,len(ordered)-cap)) if cap and len(ordered)>cap else (ordered,0)

def free_gates(pool):
    """junk -> README UUID/origin-proof -> package.json -> contents. Everything tagged, nothing deleted."""
    rows=[]
    pkg_cache={}   # repo_id -> package.json text, set by g_readme's origin proof, consumed by g_pkg (no double
                   # fetch). Kept OFF the row dict on purpose: the run-artifact CSV unions all row keys.
    for it in pool.values():
        blob=(it["full_name"]+" "+(it.get("description") or "")).lower()
        rows.append({"repo_id":str(it["id"]),"owner_repo":it["full_name"],"owner":it["owner"]["login"],
            "created_at":(it.get("created_at") or "")[:10],"pushed_at":(it.get("pushed_at") or "")[:10],
            "stars":it.get("stargazers_count",0),"homepage":(it.get("homepage") or "").strip(),
            "description":(it.get("description") or "").replace("\n"," ")[:160],
            "gate_status":"junk" if any(s in blob for s in SKIP) else "pending",
            "origin":"","markers":"","signal_deps":"","dep_count":"","platform":"","artifacts":"","readme_urls":""})
    def g_readme(r):
        a=raw(r["owner_repo"],"README.md"); b=raw(r["owner_repo"],"readme.md") if not a else ""
        if a is None and b is None: r["gate_status"]="fetch_err"; return   # transient — repo re-enters on next push
        txt=a or b or ""
        # origin verify: the editor-UUID link OR (softening) a build-level Lovable-origin proof for the no-UUID branch
        origin,pj=lovable_origin(txt,r["owner_repo"]) if ORIGIN_PROOF else (("uuid",None) if UUID.search(txt) else ("",None))
        if origin is None: r["gate_status"]="proof_err"; return   # transient proof fetch — re-enters; NOT a no_uuid drop
        if not origin:     r["gate_status"]="no_uuid";  return
        r["origin"]=origin
        if pj: pkg_cache[r["repo_id"]]=pj                         # g_pkg reuses this instead of re-fetching
        urls=[u.rstrip(".,)") for u in URL_RE.findall(txt) if not any(s in u for s in SKIP_URL)]
        r["readme_urls"]=";".join(dict.fromkeys(urls))[:300]; r["gate_status"]="pending2"
    def g_pkg(r):
        pj=pkg_cache.pop(r["repo_id"],None) or raw(r["owner_repo"],"package.json"); deps={}
        try:
            d=json.loads(pj or ""); deps={**d.get("dependencies",{}),**d.get("devDependencies",{})}
        except Exception: pass
        if not deps and raw(r["owner_repo"],"pubspec.yaml"): r["gate_status"]="native"; return
        if set(deps)&NATIVE: r["gate_status"]="native"; return
        r["platform"]="web_mobile_wrapper" if any(k.startswith(WRAP) for k in deps) else "web"
        r["dep_count"]=len(deps)
        r["signal_deps"]=";".join(sorted({k for k in deps if any(k==s or k.startswith(s) for s in SIGNAL_DEPS)}))
        r["gate_status"]="pending3"
    def g_contents(r):
        c=api(f"https://api.github.com/repos/{r['owner_repo']}/contents")
        if not isinstance(c,list): r["gate_status"]="contents_unreadable"; return
        names={x["name"] for x in c}
        r["artifacts"]=";".join(sorted({v for k,v in ARTIFACT_NAMES.items() if k in names}))
        hits=sorted(names&set(MARKERS)); other=sorted(names&OTHER_TOOL)
        if hits: r["markers"]=";".join(hits); r["gate_status"]="marker_graduate"
        elif other: r["markers"]=";".join(other); r["gate_status"]="already_tooled_other"
        else: r["gate_status"]="PASS"
    for label,fn,status in (("readme",g_readme,"pending"),("pkg",g_pkg,"pending2"),("contents",g_contents,"pending3")):
        cand=[r for r in rows if r["gate_status"]==status]
        workers=6 if label=="contents" else 3
        _CALL_PHASE[0]="gate:"+label
        with ThreadPoolExecutor(max_workers=workers) as ex: list(ex.map(fn,cand))
        print(f"[gates] after {label}: "+", ".join(f"{k}={v}" for k,v in
              sorted(__import__('collections').Counter(r['gate_status'] for r in rows).items())),flush=True)
    return rows

def score(r,today):
    """Commit-class scoring + segment assignment for one PASS row."""
    full=r["owner_repo"]
    # contributors caps at 100 identities (sorted by contributions desc, bot always present);
    # chatty anon-email repos undercount total — acceptable, they're the agency anomalies anyway
    contribs=api(f"https://api.github.com/repos/{full}/contributors?anon=1&per_page=100")
    if not isinstance(contribs,list): r["segment"]="score_err"; return r
    total=sum(x["contributions"] for x in contribs)
    bot=sum(x["contributions"] for x in contribs if x.get("login") in BOT_LOGINS
            or any(k in (x.get("email") or "") for k in LOVABLE_EMAILS) or (x.get("name") or "")=="gpt-engineer-app[bot]")
    page=api(f"https://api.github.com/repos/{full}/commits?per_page=100")
    if not isinstance(page,list) or not page: r["segment"]="score_err"; return r
    cut30=(today-timedelta(days=30)).isoformat()
    c30={"lovable_bot":0,"infra_bot":0,"web_flow":0,"local_git":0}; last={k:"" for k in c30}
    reverts=0; agent=False; days=set(); commit_emails=[]
    for c in page:
        k,ag=classify_commit(c); d=((c.get("commit") or {}).get("author") or {}).get("date","")[:10]
        agent=agent or ag
        subj=((c.get("commit") or {}).get("message") or "").split("\n")[0]
        if k=="lovable_bot" and (subj.startswith("Reverted to commit") or subj.startswith("Revert")): reverts+=1
        if k=="local_git":
            e=(((c.get("commit") or {}).get("author") or {}).get("email") or "").lower().strip()
            if e and not any(b in e for b in BADMAIL) and e not in commit_emails: commit_emails.append(e)
        if not last[k]: last[k]=d
        if d>=cut30:
            c30[k]+=1
            if k in ("local_git","lovable_bot"): days.add(d)
    def age(s):
        try: return (today-datetime.strptime(s,"%Y-%m-%d").date()).days
        except Exception: return 9999
    last_bot_age=age(last["lovable_bot"]) if last["lovable_bot"] else 9999
    if not last["lovable_bot"] and bot>0 and len(page)==100:
        last_bot_age=46   # bot exists but got pushed off the newest-100 page by human activity:
                          # recency unknown — treat as "just past the C window", not ancient (avoids
                          # mislabeling the hottest movers presumed_silent_graduate)
    share=round(bot/total,3) if total else 0
    seriousness=bool(r["signal_deps"]) or "own_backend" in (r["artifacts"] or "") or "tests" in (r["artifacts"] or "") or bot>=300
    substant=total>=10 or len(days)>=3            # C substance floor (backtest lesson)
    # segment cascade (pure, testable). edge_detect fires LAZILY only in the B branch, so no
    # A/C/PSG/shadow/other row ever pays the extra API call or can be flipped by it.
    def _edge_detect():                            # returns (hosts,secrets) if integrated, else None
        h,s=edge_api_integration(r["owner_repo"]); return (h,s) if (h or s) else None
    seg,edge=assign_segment(agent,last_bot_age,c30["local_git"],bot,substant,age(r["created_at"]),share,seriousness,_edge_detect)
    if edge:                                        # B_light -> B_all_bot via edge-fn integration
        h,s=edge; r["signal_deps"]=";".join(filter(None,[r.get("signal_deps",""),"edge:"+";".join(sorted(h|s)[:4])]))
    commit_emails.sort(key=lambda e:0 if r["owner"].lower().split("-")[0] in e else 1)  # owner-affinity first (daily_poll parity)
    r.update({"total_commits":total,"bot_total":bot,"bot_share":share,"segment":seg,
              "c30_bot":c30["lovable_bot"],"c30_local":c30["local_git"],"last_bot":last["lovable_bot"],
              "last_local":last["local_git"],"reverts_in_100":reverts,"active_days_30":len(days),
              "commit_emails":";".join(commit_emails[:3])})
    time.sleep(0.25); return r

def enrich_B(r,today):
    """True first-commit age + 12wk trend (2 window counts). Only for B candidates."""
    full=r["owner_repo"]
    resp,body=api(f"https://api.github.com/repos/{full}/commits?per_page=1",want_resp=True)
    first=""
    if resp is not None:
        m=re.search(r'[?&]page=(\d+)>; rel="last"',resp.headers.get("Link",""))
        if m:
            lastp=api(f"https://api.github.com/repos/{full}/commits?per_page=1&page={m.group(1)}")
            if lastp: first=lastp[0]["commit"]["author"]["date"][:10]
        elif body: first=body[0]["commit"]["author"]["date"][:10]
    r["first_commit"]=first
    r["true_age_days"]=(today-datetime.strptime(first,"%Y-%m-%d").date()).days if first else ""
    r4=link_count(full,(today-timedelta(days=28)).isoformat())
    b8=link_count(full,(today-timedelta(days=84)).isoformat(),(today-timedelta(days=28)).isoformat())
    if r4 is None or b8 is None: r["trend"]="err"
    elif r4+b8<8: r["trend"]="sparse"
    elif b8==0: r["trend"]="new_activity"
    else:
        ratio=(r4/4.0)/(b8/8.0)
        r["trend"]="increasing" if ratio>=1.3 else "dropping" if ratio<=0.7 else "steady"
    if r["segment"]=="B_all_bot":
        if isinstance(r["true_age_days"],int) and r["true_age_days"]<60: r["segment"]="B_under_60d"
        elif not first: r["segment"]="B_age_unknown"   # age floor fails CLOSED, not open
    time.sleep(0.3); return r

def contact_pass(r,scrape_sites=False):
    """Profile + commit emails + README-resolved live URL (+ optional firecrawl on the site)."""
    prof=api(f"https://api.github.com/users/{r['owner']}") or {}
    urls=[u for u in (r.get("readme_urls") or "").split(";") if u]
    if r.get("homepage"): urls.insert(0,r["homepage"])
    if prof.get("blog") and len(prof["blog"])>8: urls.append(prof["blog"].strip())
    live=urls[0] if urls else ""
    email=prof.get("email") or (r.get("commit_emails") or "").split(";")[0]
    paths=[]
    if prof.get("email"): paths.append(f"profile_email:{prof['email']}")
    if r.get("commit_emails"): paths.append(f"commit_email:{r['commit_emails'].split(';')[0]}")
    if prof.get("twitter_username"): paths.append(f"x:@{prof['twitter_username']}")
    if live: paths.append(f"app:{live[:60]}")
    if not email and scrape_sites and live:
        try:
            # per-repo temp file + returncode check: a shared/stale file cross-attributes
            # ANOTHER lead's site email — the one failure mode worse than no email
            out=os.path.join(DATA,f".fc_{re.sub(r'[^a-z0-9]+','-',r['owner_repo'].lower())}.md")
            proc=subprocess.run(["firecrawl","scrape",live,"-o",out],capture_output=True,timeout=90)
            md=open(out).read() if (proc.returncode==0 and os.path.exists(out)) else ""
            if os.path.exists(out): os.remove(out)
            found=[e for e in set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,10}",md))
                   if not any(b in e.lower() for b in BADMAIL+("example","sentry",".png",".svg","placeholder"))]
            if found: email=sorted(found)[0]; paths.insert(0,f"site_email:{email}")
        except Exception: pass
    r.update({"email":email or "","x_handle":prof.get("twitter_username") or "","live_url":live,
              "owner_location":prof.get("location") or "","contact_paths":" | ".join(paths)})
    time.sleep(0.3); return r

# ---------------- main ----------------
def run(since,dry,no_contact,scrape_sites,score_cap=200):
    _auth()
    rundate=date.today().isoformat(); today=date.today(); t0=time.time()
    pool=pull(since,rundate)
    rows=free_gates(pool)

    st=store_load()
    grad_store={r["owner_repo"].lower() for r in (csv.DictReader(open(os.path.join(ROOT,"store","seen_store.csv")))
                if os.path.exists(os.path.join(ROOT,"store","seen_store.csv")) else [])}
    wl_owners={r["owner_repo"].split("/")[0].lower() for r in (csv.DictReader(open(os.path.join(ROOT,"outreach","worklist_repo.csv")))
               if os.path.exists(os.path.join(ROOT,"outreach","worklist_repo.csv")) else []) if r.get("owner_repo")}

    # store cross-ref: stamp graduations; select net-new PASS rows for scoring
    netnew=[]; converted=[]; gate_routed=[]
    for r in rows:
        s=st.get(r["repo_id"])
        if s:
            s["last_seen"]=rundate
            runs=set(filter(None,s.get("source_runs","").split(";"))); runs.add(f"daily-{rundate}")
            s["source_runs"]=";".join(sorted(runs)); s["times_seen"]=str(len(runs))
            if r["gate_status"]=="marker_graduate" and not s.get("graduated_on"):
                s["graduated_on"]=rundate; s["status"]="graduated"; converted.append(r["owner_repo"])
                print(f"[convert] {r['owner_repo']} GRADUATED (marker appeared) — was {s.get('segment')}",flush=True)
            continue
        if r["gate_status"] in ("marker_graduate","already_tooled_other"):
            gate_routed.append(r); continue          # store these so they stop re-paying gate calls daily
        if r["gate_status"]!="PASS": continue
        if r["owner_repo"].lower() in grad_store: r["gate_status"]="in_graduate_store"; continue
        if r["owner"].lower() in wl_owners: r["gate_status"]="owner_in_worklist"; continue
        netnew.append(r)
    print(f"[score] net-new PASS to score: {len(netnew)}",flush=True)
    netnew,deferred=cap_netnew(netnew,score_cap)
    if deferred: print(f"[cap] scoring {len(netnew)} oldest-pushed; deferring {deferred} un-stored -> re-scored next run (--score-cap {score_cap})",flush=True)

    _CALL_PHASE[0]="score"
    with ThreadPoolExecutor(max_workers=4) as ex: netnew=list(ex.map(lambda r:score(r,today),netnew))
    bcand=[r for r in netnew if r.get("segment")=="B_all_bot"]   # B_light is routed — don't spend enrich calls on it
    _CALL_PHASE[0]="enrich_B"
    with ThreadPoolExecutor(max_workers=3) as ex: list(ex.map(lambda r:enrich_B(r,today),bcand))

    qualified=[r for r in netnew if r.get("segment") in ("A_hybrid","B_all_bot","C_mover","C_mover_fresh")]
    if not no_contact:
        _CALL_PHASE[0]="contact"
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(lambda r:contact_pass(r,scrape_sites),qualified))

    # run artifact (everything, prunable)
    outcsv=os.path.join(DATA,f"ceiling_daily_{rundate}.csv")
    allfields=sorted({k for r in rows for k in r})
    with open(outcsv,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=allfields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

    from collections import Counter
    print(f"[s] segments: {dict(Counter(r.get('segment','-') for r in netnew))}",flush=True)
    print("[calls] "+" ".join(f"{p}={c[0]}c/{c[1]}r" for p,c in sorted(CALL_COUNTS.items()))
          +f" | TOTAL {sum(c[0] for c in CALL_COUNTS.values())}core/{sum(c[1] for c in CALL_COUNTS.values())}raw",flush=True)
    if dry:
        print(f"[dry-run] no store/worklist writes. run artifact -> {os.path.relpath(outcsv,ROOT)}",flush=True)
        return

    # WORKLIST FIRST, store last: a crash between the two must lose the dedup (repos re-score
    # next run — idempotent) rather than the leads (unrecoverable once the store skips them).
    wl=[{"first_seen":rundate,"segment":r["segment"],"trend":r.get("trend",""),"bot_total":r.get("bot_total",""),
         "true_age_days":r.get("true_age_days",""),"c30_bot":r.get("c30_bot",""),"c30_local":r.get("c30_local",""),
         "last_bot":r.get("last_bot",""),"reverts_in_100":r.get("reverts_in_100",""),
         "signal_deps":r.get("signal_deps",""),"product":r.get("description",""),"live_url":r.get("live_url",""),
         "email":r.get("email",""),"x_handle":r.get("x_handle",""),"contact_paths":r.get("contact_paths",""),
         "owner_location":r.get("owner_location",""),"owner_repo":r["owner_repo"],
         "github_url":f"https://github.com/{r['owner_repo']}"}
        for r in qualified if r.get("email") or r.get("x_handle")]
    added,total=worklist_upsert(wl)
    retired=worklist_retire(set(converted))
    if retired: print(f"[convert] retired {retired} graduated worklist row(s)",flush=True)

    # store upsert. NOT stored: score_err rows (transient API failures — a stored err would
    # never re-score; the repo re-enters on its next push) and, under --no-contact, qualified
    # rows (they'd be skipped forever with empty contact; let the next full run pick them up).
    for r in gate_routed:
        st[r["repo_id"]]={"repo_id":r["repo_id"],"owner_repo":r["owner_repo"],"owner":r["owner"],
            "first_seen":rundate,"last_seen":rundate,"times_seen":"1","segment":r["gate_status"],
            "bot_total":"","bot_share":"","trend":"","best_contact":"",
            "status":"graduate_stream" if r["gate_status"]=="marker_graduate" else "routed",
            "graduated_on":"","source_runs":f"daily-{rundate}"}
    skipped_q=0
    for r in netnew:
        if r.get("segment")=="score_err": continue
        if no_contact and r.get("segment") in ("A_hybrid","B_all_bot","C_mover","C_mover_fresh"):
            skipped_q+=1; continue
        seg=r.get("segment","")
        st[r["repo_id"]]={"repo_id":r["repo_id"],"owner_repo":r["owner_repo"],"owner":r["owner"],
            "first_seen":rundate,"last_seen":rundate,"times_seen":"1","segment":seg,
            "bot_total":r.get("bot_total",""),"bot_share":r.get("bot_share",""),"trend":r.get("trend",""),
            "best_contact":(r.get("contact_paths","") or "").split(" | ")[0],
            "status":"" if seg in ("A_hybrid","B_all_bot","C_mover","C_mover_fresh") else "routed",
            "graduated_on":"","source_runs":f"daily-{rundate}"}
    store_save(st)
    if skipped_q: print(f"[no-contact] {skipped_q} qualified rows NOT stored — next full run picks them up",flush=True)
    print(f"[done {int(time.time()-t0)}s] store {len(st)} | worklist +{added} -> {total} "
          f"({os.path.relpath(WORKLIST,ROOT)}) | run artifact {os.path.relpath(outcsv,ROOT)}",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--since",default=(date.today()-timedelta(days=1)).isoformat())
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--no-contact",action="store_true")
    ap.add_argument("--scrape-sites",action="store_true",help="firecrawl the live app when no email found (needs firecrawl CLI)")
    ap.add_argument("--score-cap",type=int,default=200,help="max net-new repos scored per run (oldest-pushed first; rest defer to a later run). Set high on a measurement dry-run.")
    ap.add_argument("--stats",action="store_true")
    a=ap.parse_args()
    if a.stats: store_stats(); sys.exit(0)
    run(a.since,a.dry_run,a.no_contact,a.scrape_sites,a.score_cap)

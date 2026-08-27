#!/usr/bin/env python3
"""test_scoring.py - offline unit tests for the edge-function seriousness signal and the segment
cascade scoping. No network, no auth. Run: python3 pipeline/test_scoring.py

Covers: (1) edge_api_integration detects non-Lovable third-party integrations in Supabase edge
functions, ignores Lovable's own AI gateway and benign env vars, and NEVER raises (it runs in the
unguarded score() thread pool); (2) assign_segment probes the edge detector LAZILY only in the B
branch, so no A/C/PSG/shadow/other row can pay the API call or be flipped."""
import os, sys, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ceiling_poll as cp

PASS=0; FAIL=0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS+=1; print(f"  ok   {name}")
    else: FAIL+=1; print(f"  FAIL {name}")

# ---------- edge_api_integration ----------
def run_edge(paths, bodies, raise_on=None):
    cp.api = lambda url, *a, **k: ({"tree":[{"type":"blob","path":p} for p in paths]} if "git/trees" in url else None)
    def _raw(full, p, *a, **k):
        if raise_on and p==raise_on: raise ValueError("boom")
        return bodies.get(p, "")
    cp.raw = _raw
    return cp.edge_api_integration("owner/repo")

print("edge_api_integration:")
h,s = run_edge(["supabase/functions/quote/index.ts"],
               {"supabase/functions/quote/index.ts": 'const r = await fetch("https://api.finnhub.io/v1/quote");'})
check("external host (finnhub) detected", "api.finnhub.io" in h)

h,s = run_edge(["supabase/functions/ai/index.ts"],
               {"supabase/functions/ai/index.ts": 'fetch("https://ai.gateway.lovable.dev/v1/chat"); Deno.env.get("LOVABLE_API_KEY")'})
check("Lovable-gateway-only -> empty (no flip)", not h and not s)

h,s = run_edge(["supabase/functions/x/index.ts"],
               {"supabase/functions/x/index.ts": 'Deno.env.get("ENVIRONMENT"); Deno.env.get("PORT"); Deno.env.get("ALLOWED_ORIGINS");'})
check("benign infra env vars -> empty", not h and not s)

h,s = run_edge(["supabase/functions/pay/index.ts"],
               {"supabase/functions/pay/index.ts": 'const k = Deno.env.get("STRIPE_SECRET_KEY");'})
check("vendor secret (STRIPE) detected", any("STRIPE" in x for x in s))

h,s = run_edge(["src/App.tsx"], {"src/App.tsx": 'fetch("https://api.finnhub.io")'})
check("no supabase/functions -> empty (client fetch not counted)", not h and not s)

h,s = run_edge(["supabase/functions/x/index.ts"], {}, raise_on="supabase/functions/x/index.ts")
check("raw() raising -> caught, returns empty (never propagates)", not h and not s)

cp.api = lambda *a, **k: None                       # tree unreadable / truncated
check("tree unreadable -> empty (fail-closed)", cp.edge_api_integration("o/r")==(set(),set()))

# ---------- assign_segment scoping ----------
print("assign_segment:")
called={"n":0}
def edge_true(): called["n"]+=1; return ({"finnhub.io"}, {"FINNHUB_API_KEY"})   # real detector returns (hosts,secrets) tuple
def edge_false(): called["n"]+=1; return None

called["n"]=0
seg,edge = cp.assign_segment(False, 3, 0, 500, True, 400, 0.99, False, edge_true)
h,sec = edge   # must be unpackable — score() does `h,s=edge` (regression: a bool marker crashed here)
check("B cand + edge integration -> B_all_bot, marker unpacks", seg=="B_all_bot" and h=={"finnhub.io"} and called["n"]==1)

called["n"]=0
seg,edge = cp.assign_segment(False, 3, 0, 500, True, 400, 0.99, False, edge_false)
check("B cand + no integration -> B_light", seg=="B_light" and not edge and called["n"]==1)

called["n"]=0
seg,edge = cp.assign_segment(False, 3, 0, 500, True, 400, 0.99, True, edge_true)
check("B cand + pkg seriousness -> B_all_bot, no probe", seg=="B_all_bot" and called["n"]==0)

called["n"]=0
seg,edge = cp.assign_segment(False, 10, 5, 20, True, 200, 0.99, False, edge_true)
check("A_hybrid path never calls edge (scoping)", seg=="A_hybrid" and called["n"]==0)

called["n"]=0
seg,edge = cp.assign_segment(True, 3, 0, 500, True, 400, 0.99, False, edge_true)
check("shadow_graduate never calls edge", seg=="shadow_graduate" and called["n"]==0)

called["n"]=0
seg,edge = cp.assign_segment(False, 60, 3, 10, True, 200, 0.5, False, edge_true)
check("presumed_silent_graduate never calls edge", seg=="presumed_silent_graduate" and called["n"]==0)

# ---------- lovable_origin (no_uuid origin-proof gate) ----------
# Softening the no_uuid gate: when a search hit's README lacks the editor-UUID link, accept it if
# package.json carries lovable-tagger (parsed dep) or index.html carries the gpteng CDN. Returns
# (origin, pkg_text); origin=""=definitely-no-proof (=>no_uuid), None=transient (=>proof_err). Never raises.
print("lovable_origin:")
def run_origin(txt, files, raise_on=None):
    """Monkeypatch cp.raw; call cp.lovable_origin. files: {path: raw_return}; absent path -> "" (404).
    Pass None as a value to simulate a transient fetch error (raw()'s None contract)."""
    def _raw(full, p, *a, **k):
        if raise_on and p==raise_on: raise ValueError("boom")
        return files.get(p, "")
    cp.raw=_raw
    return cp.lovable_origin(txt, "owner/repo")

UUID_README="Editor https://lovable.dev/projects/12345678-1234-4abc-8def-1234567890ab — live at https://myapp.io"
NOUUID_README="Built on lovable.dev/projects. Live demo: https://myapp.io"

check("UUID readme -> ('uuid', None)", run_origin(UUID_README, {})==("uuid",None))
o=run_origin(NOUUID_README, {"package.json":'{"devDependencies":{"lovable-tagger":"^1.1.0"}}'})
check("no-UUID + lovable-tagger dep -> ('tagger', text)", o[0]=="tagger" and "lovable-tagger" in (o[1] or ""))
o=run_origin(NOUUID_README, {"package.json":'{"dependencies":{"react":"^18"}}',"index.html":"<html><body>hi</body></html>"})
check("no-UUID + real pkg w/o tagger + plain index.html -> '' (=> no_uuid)", o[0]=="")
o=run_origin(NOUUID_README, {"package.json":None,"index.html":None})
check("no-UUID + transient pkg + transient index.html -> (None,None) (=> proof_err)", o==(None,None))
o=run_origin(NOUUID_README, {"package.json":""})    # 404 pkg; index.html absent -> "" (404)
check("no-UUID + 404 pkg + no index.html -> '' (=> no_uuid)", o[0]=="")
o=run_origin(NOUUID_README, {"package.json":'{"devDependencies":{"lovable-tagger"::}}'})  # malformed JSON w/ substring
check("malformed pkg w/ 'lovable-tagger' substring -> NOT tagger (parse-based)", o[0]!="tagger")
o=run_origin(NOUUID_README, {}, raise_on="package.json")
check("raw() raising -> caught, undetermined (None,..) never propagates", o[0] is None)
o=run_origin(NOUUID_README, {"package.json":'{"dependencies":{"react":"^18"}}',
                             "index.html":'<script src="https://cdn.gpteng.co/gptengineer.js"></script>'})
check("no-UUID + no tagger + gpteng in index.html -> ('gpteng', ..)", o[0]=="gpteng")

# ---------- free_gates wiring (origin column, pending2 invariant, pkg_cache reuse, routing) ----------
print("free_gates wiring:")
def run_gates(files, contents, desc=""):
    """One-repo free_gates run. files: {path: raw_return} (absent -> "" ; None -> transient).
    contents: list for the /contents api call. Returns (row, raw_call_counts)."""
    rc={}
    def _raw(full, p, *a, **k):
        rc[p]=rc.get(p,0)+1
        return files.get(p, "")
    def _api(url, *a, **k):
        return contents if url.endswith("/contents") else None
    cp.raw=_raw; cp.api=_api
    pool={1:{"id":1,"full_name":"owner/repo","owner":{"login":"owner"},"description":desc,
             "created_at":"2026-06-01T00:00:00Z","pushed_at":"2026-07-10T00:00:00Z",
             "stargazers_count":0,"homepage":""}}
    with contextlib.redirect_stdout(io.StringIO()): rows=cp.free_gates(pool)
    return rows[0], rc

TAGGER_PKG='{"devDependencies":{"lovable-tagger":"^1.1.0"},"dependencies":{"react":"^18"}}'

row,rc=run_gates({"README.md":UUID_README,"package.json":TAGGER_PKG}, [{"name":"src"}])
check("UUID path -> PASS + origin=uuid", row["gate_status"]=="PASS" and row.get("origin")=="uuid")
check("UUID path harvests readme_urls", "myapp.io" in row.get("readme_urls",""))
check("UUID path adds NO proof fetch (pkg once in g_pkg, no index.html)", rc.get("package.json")==1 and rc.get("index.html",0)==0)

row,rc=run_gates({"README.md":NOUUID_README,"package.json":TAGGER_PKG}, [{"name":"src"}])
check("no-UUID tagger -> PASS (pending2 invariant holds)", row["gate_status"]=="PASS")
check("no-UUID tagger -> origin=tagger + readme_urls harvested", row.get("origin")=="tagger" and "myapp.io" in row.get("readme_urls",""))
check("pkg_cache reuse: package.json fetched once, not double", rc.get("package.json")==1)
check("no cache/private field leaks onto the row (CSV union safety)", not any("cache" in k.lower() or k.startswith("_") for k in row))

row,rc=run_gates({"README.md":NOUUID_README,"package.json":TAGGER_PKG}, [{"name":"CLAUDE.md"}])
check("no-UUID tagger + CLAUDE.md marker -> marker_graduate (routes OUT)", row["gate_status"]=="marker_graduate" and row.get("origin")=="tagger")

row,rc=run_gates({"README.md":NOUUID_README,"package.json":'{"dependencies":{"react":"^18"}}',"index.html":"<html></html>"}, [{"name":"src"}])
check("no-UUID + no proof -> no_uuid", row["gate_status"]=="no_uuid")

row,rc=run_gates({"README.md":NOUUID_README,"package.json":None,"index.html":None}, [{"name":"src"}])
check("no-UUID + transient proof -> proof_err (distinct from no_uuid)", row["gate_status"]=="proof_err")

# ---------- assign_segment C_mover_fresh bot>0 (coupled fix: softening makes bot==0 reachable) ----------
print("assign_segment C_mover_fresh:")
seg,_=cp.assign_segment(False, 9999, 2, 0, True, 20, 0.0, False, edge_false)   # fresh, hand-committed, ZERO bot history
check("C_mover_fresh with bot==0 -> NOT C_mover_fresh", seg!="C_mover_fresh")
seg,_=cp.assign_segment(False, 46, 2, 3, True, 20, 0.5, False, edge_false)     # fresh, carries migrated (bot>0) history
check("C_mover_fresh with bot>0 -> C_mover_fresh (unchanged)", seg=="C_mover_fresh")

# ---------- cap_netnew (per-run score cap, oldest-pushed first) ----------
print("cap_netnew:")
_rows=[{"owner_repo":f"o/r{i}","pushed_at":p} for i,p in enumerate(["2026-07-12","2026-07-10","2026-07-11","2026-07-09"])]
kept,deferred=cp.cap_netnew(_rows, 2)
check("cap keeps N oldest-pushed (pushed_at ASC)", [r["pushed_at"] for r in kept]==["2026-07-09","2026-07-10"] and deferred==2)
kept,deferred=cp.cap_netnew(_rows, 10)
check("cap above len -> keeps all, defers 0", len(kept)==4 and deferred==0)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

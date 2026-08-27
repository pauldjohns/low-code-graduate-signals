#!/usr/bin/env python3
"""
social_pull.py - multi-channel social signal pull for Lovable graduates / in-market people.
Normalizes Reddit + Hacker News + Dev.to + Mastodon into one schema, dedups by url.
Output: data/social_raw_{rundate}.json (+ .csv). Feed to the classification workflow.

Channels: Reddit (RSS), Hacker News (Algolia), Dev.to (Forem), Mastodon (tag timelines), Bluesky (api.bsky.app).
Gaps (login-walled / no free search): X/Twitter, LinkedIn, Discord (ToS / requires joining). Reddit .json is 403; RSS works.

Run:  python3 pipeline/social_pull.py
Schema: source, author, author_url, date, title, text, url, query
"""
import csv, html, json, os, re, time, urllib.parse, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
DATA=os.path.join(ROOT,"data"); os.makedirs(DATA, exist_ok=True)
UA = os.environ.get("USER_AGENT", "signal-research/0.4")
def strip(t): return re.sub(r"\s+"," ", html.unescape(re.sub("<[^>]+>"," ", t or ""))).strip()
def get(url, headers=None, timeout=25):
    try: return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {"User-Agent":UA}), timeout=timeout).read()
    except Exception: return None

items=[]

# ---------- Reddit (RSS; body in <content>) ----------
NS={"a":"http://www.w3.org/2005/Atom"}
REDDIT=[("lovable",q) for q in ("migrate","leaving","export","cursor","claude+code","codex","credits","supabase")] + \
       [("vibecoding","lovable"),("ChatGPTCoding","lovable"),("cursor","lovable"),
        ("ClaudeAI","lovable"),("SideProject","lovable"),("nocode","lovable"),("SaaS","lovable")]
def reddit():
    n=0
    for sub,q in REDDIT:
        url=f"https://www.reddit.com/r/{sub}/search.rss?q={q}&restrict_sr=1&sort=new&limit=25"
        data=None
        for _ in range(3):
            data=get(url, {"User-Agent":UA})
            if data and data.lstrip().startswith(b"<"): break
            time.sleep(12)
        if not data: continue
        try: root=ET.fromstring(data)
        except Exception: continue
        for e in root.findall("a:entry",NS):
            ln=e.find("a:link",NS); t=e.find("a:title",NS); au=e.find("a:author/a:name",NS)
            up=e.find("a:updated",NS); co=e.find("a:content",NS)
            items.append({"source":f"reddit:r/{sub}","author":au.text if au is not None else "",
                "author_url":(f"https://www.reddit.com/user/{au.text.split('/')[-1]}" if au is not None and au.text else ""),
                "date":(up.text[:10] if up is not None else ""),"title":strip(t.text) if t is not None else "",
                "text":strip(co.text)[:1800] if co is not None else "","url":ln.get("href") if ln is not None else "","query":q})
            n+=1
        time.sleep(12)
    return n

# ---------- Hacker News (Algolia, free) ----------
HN_QUERIES=[("\"lovable.dev\"","story"),("\"lovable.dev\"","comment"),
            ("lovable cursor","comment"),("lovable \"claude code\"","comment"),
            ("lovable migrate","comment"),("lovable export","comment")]
def hn():
    n=0
    for q,tag in HN_QUERIES:
      for page in (0,1):  # paginate so full result set (e.g. 68 "lovable.dev" comments) is captured
        url=f"https://hn.algolia.com/api/v1/search_by_date?query={urllib.parse.quote(q)}&tags={tag}&hitsPerPage=50&page={page}"
        data=get(url)
        if not data: break
        try: hits=json.loads(data).get("hits",[])
        except Exception: break
        if not hits: break
        for h in hits:
            txt=h.get("title") or h.get("story_title") or strip(h.get("comment_text",""))
            ts=h.get("created_at_i"); d=datetime.fromtimestamp(ts,timezone.utc).date().isoformat() if ts else h.get("created_at","")[:10]
            items.append({"source":"hackernews","author":h.get("author",""),
                "author_url":f"https://news.ycombinator.com/user?id={h.get('author','')}",
                "date":d,"title":h.get("title") or h.get("story_title") or "",
                "text":(strip(h.get("comment_text","")) or txt)[:1800],
                "url":h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}","query":q})
            n+=1
        time.sleep(1)
    return n

# ---------- Dev.to (Forem API, free) ----------
def devto():
    n=0
    for tag in ("lovable","vibecoding"):
        data=get(f"https://dev.to/api/articles?tag={tag}&per_page=30")
        if not data: continue
        try: arts=json.loads(data)
        except Exception: continue
        for a in arts:
            items.append({"source":"devto","author":(a.get("user") or {}).get("username",""),
                "author_url":(a.get("user") or {}).get("website_url") or f"https://dev.to/{(a.get('user') or {}).get('username','')}",
                "date":(a.get("published_at") or "")[:10],"title":a.get("title",""),
                "text":(a.get("description") or "")[:1800],"url":a.get("url",""),"query":f"tag:{tag}"})
            n+=1
        time.sleep(1)
    return n

# ---------- Mastodon (public tag timelines) ----------
MASTO=["mastodon.social","fosstodon.org","hachyderm.io"]
def mastodon():
    n=0
    for inst in MASTO:
        for tag in ("lovable","vibecoding"):
            data=get(f"https://{inst}/api/v1/timelines/tag/{tag}?limit=20",{"User-Agent":UA})
            if not data: continue
            try: toots=json.loads(data)
            except Exception: continue
            if not isinstance(toots,list): continue
            for t in toots:
                acct=t.get("account") or {}
                origin=(acct.get("url","").split("/")[2] if acct.get("url","").startswith("http") else inst)
                items.append({"source":f"mastodon:{origin}","author":acct.get("acct",""),
                    "author_url":acct.get("url",""),"date":(t.get("created_at") or "")[:10],
                    "title":"","text":strip(t.get("content",""))[:1800],"url":t.get("url",""),"query":f"tag:{tag}"})
                n+=1
            time.sleep(1)
    return n

# ---------- Bluesky (AT Protocol AppView - free via api.bsky.app, NOT public.api.bsky.app) ----------
# High-precision queries ONLY: bare "lovable" is an English adjective (>90% false positives).
BSKY_Q=["lovable.dev","lovable supabase","lovable.dev claude","lovable.dev cursor","lovable migrate"]
def bluesky():
    n=0
    for q in BSKY_Q:
        data=get(f"https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={urllib.parse.quote(q)}&limit=25")
        if not data: continue
        try: posts=json.loads(data).get("posts",[])
        except Exception: continue
        for p in posts:
            a=p.get("author") or {}; handle=a.get("handle",""); rec=p.get("record") or {}
            rkey=(p.get("uri","").split("/")[-1] if p.get("uri") else "")
            items.append({"source":"bluesky","author":handle,"author_url":f"https://bsky.app/profile/{handle}",
                "date":(rec.get("createdAt") or "")[:10],"title":"","text":strip(rec.get("text",""))[:1800],
                "url":f"https://bsky.app/profile/{handle}/post/{rkey}","query":q})
            n+=1
        time.sleep(1)
    return n

rundate=date.today().isoformat()
print(f"[social {rundate}] pulling...",flush=True)
for name,fn in [("reddit",reddit),("hackernews",hn),("devto",devto),("mastodon",mastodon),("bluesky",bluesky)]:
    try: c=fn(); print(f"  {name}: {c}",flush=True)
    except Exception as ex: print(f"  {name}: ERROR {ex}",flush=True)

# dedup by url
seen={};
for it in items:
    if it["url"] and it["url"] not in seen: seen[it["url"]]=it
rows=list(seen.values())
cols=["source","author","author_url","date","title","text","url","query"]
with open(os.path.join(DATA,f"social_raw_{rundate}.csv"),"w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)
json.dump(rows,open(os.path.join(DATA,f"social_raw_{rundate}.json"),"w"))
from collections import Counter
print(f"[social {rundate}] total unique: {len(rows)}",flush=True)
print("  by source:",dict(Counter(r['source'].split(':')[0] for r in rows)),flush=True)
print(f"  wrote data/social_raw_{rundate}.json (+ .csv)",flush=True)

#!/usr/bin/env python3
"""
build_dashboard.py - generate outreach/dashboard.html, a filterable, checkbox-tracked
view of the two worklists.

Two ways to use it:
  - FILE mode (no server): double-click outreach/dashboard.html. Tracking (messaged +
    notes) persists in the browser via localStorage; "Save to worklist" downloads
    tracking.json -> run apply_tracking.py to push it into the CSVs.
  - SERVER mode: python3 pipeline/serve_dashboard.py -> open http://127.0.0.1:8787.
    Edits write straight to the worklist CSVs (no export/apply step). The page detects
    it is served over http and switches to live saving automatically.

build_rows() and render_html() are imported by serve_dashboard.py so the served page is
always rendered fresh from the current CSVs. Run this script after build_worklist.py to
refresh the static file.
"""
import csv, json, os, datetime
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
def rd(p):
    fp=os.path.join(ROOT,p); return list(csv.DictReader(open(fp))) if os.path.exists(fp) else []

# post-age staleness: a social lead's value decays with the POST's age — you can't engage an
# archived thread. Windows per platform in days (the operator's anchors: HN ~2wk, Reddit ~2mo;
# Mastodon/Bluesky and Dev.to are sensible defaults). A lead older than its window = "stale".
FRESH_DAYS={"hackernews":14,"reddit":60,"mastodon":30,"bluesky":30,"devto":90}
DEFAULT_FRESH=60
def _age_days(post_date):
    try: return (datetime.date.today()-datetime.date.fromisoformat((post_date or "")[:10])).days
    except Exception: return None

def build_rows():
    rows=[]
    for r in rd("outreach/worklist_repo.csv"):
        rows.append({"type":"repo","id":r["owner_repo"],
            "tier":r.get("tier",""),"score":r.get("score",""),"region":r.get("region",""),
            "country":r.get("country",""),"confidence":r.get("confidence",""),
            "name":r["owner_repo"],"name_url":r.get("github_url",""),
            "contact":r.get("email",""),"contact_kind":"email",
            "context":r.get("product",""),"sub":r.get("evidence","")[:160],
            "live":r.get("live_app",""),"first_seen":r.get("first_seen",""),
            "seed_status":r.get("status",""),"seed_notes":r.get("notes","")})
    for r in rd("outreach/worklist_ceiling.csv"):
        # CEILING = pre-grad builders (method/CEILING.md). Contact: email first, X fallback.
        contact=r.get("email","") or (("https://x.com/"+r["x_handle"].lstrip("@")) if r.get("x_handle") else "")
        seg=(r.get("segment","") or "")
        segk="B" if seg.startswith("B") else "A" if seg.startswith("A") else "C"
        age=r.get("true_age_days","")
        rows.append({"type":"ceiling","id":r["owner_repo"],
            "segment":seg,"seg_key":segk,"trend":r.get("trend",""),
            "bot_total":r.get("bot_total",""),"tier":"","score":"","region":"",
            "country":"","confidence":"",
            "name":r["owner_repo"],"name_url":r.get("github_url",""),
            "contact":contact,"contact_kind":"email" if r.get("email") else "link",
            "context":r.get("product","") or r["owner_repo"].split("/")[1],
            "sub":" · ".join(x for x in (f"{r.get('bot_total','')} prompts",
                (f"{age}d old" if age else ""),r.get("signal_deps",""),r.get("owner_location","")) if x)[:160],
            "live":r.get("live_url",""),"first_seen":r.get("first_seen",""),
            "seed_status":r.get("status",""),"seed_notes":r.get("notes","")})
    for r in rd("outreach/worklist_social.csv"):
        post=r.get("post_url","")
        # SOCIAL action = engage the actual post. "Who" -> their profile (handle); the Contact
        # column links the POST itself (+ a resolved email iff one exists). The resolved
        # domain/github from social_link is low-yield enrichment and is NOT surfaced here.
        plat=r.get("platform",""); pdate=r.get("post_date",""); age=_age_days(pdate)
        win=FRESH_DAYS.get(plat,DEFAULT_FRESH)
        rows.append({"type":"social","id":post,
            "tier":"","score":r.get("warm_score",""),"region":"",
            "country":"","confidence":"","classification":r.get("classification",""),
            "contactable":r.get("contactable",""),"platform":plat,
            "name":r.get("author",""),"name_url":r.get("outreach_handle",""),
            "post_url":post,
            "contact":r.get("resolved_email",""),"contact_kind":"email",
            "context":(r.get("wedge_quote","") or ""),"sub":f"{r.get('trigger','')} → {r.get('destination','')}",
            "live":post,"first_seen":r.get("first_seen",""),
            "post_date":pdate,"age_days":(age if age is not None else ""),
            "stale":(age is not None and age>win),
            "seed_status":r.get("status",""),"seed_notes":r.get("notes","")})
    # actionable (fresh) social leads first, stale sink to the bottom
    repo_rows=[r for r in rows if r["type"]=="repo"]
    ceil_rows=[r for r in rows if r["type"]=="ceiling"]   # worklist file order = segment/trend rank
    soc_rows=[r for r in rows if r["type"]=="social"]
    soc_rows.sort(key=lambda d:(1 if d["stale"] else 0,
        0 if str(d["contactable"]).lower()=="true" else 1, -int(d["score"] or 0)))
    return repo_rows+ceil_rows+soc_rows

TEMPLATE=r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lovable-Graduate Targets</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e7eaf0;--mut:#8b94a7;--acc:#6ea8fe;--ok:#3ecf8e;--warn:#f0b357;--repo:#6ea8fe;--social:#c08cf0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:18px 22px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
h1{margin:0 0 6px;font-size:18px}.sub{color:var(--mut);font-size:12px}
.stats{display:flex;gap:18px;margin-top:10px;flex-wrap:wrap}.stat b{font-size:18px}.stat span{color:var(--mut);font-size:11px;display:block}
.controls{display:flex;gap:8px;align-items:center;padding:12px 22px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{padding:6px 14px;border:1px solid var(--line);border-radius:20px;cursor:pointer;color:var(--mut);background:var(--card)}
.tab.on{color:var(--fg);border-color:var(--acc);background:#1d2738}
input,select{background:var(--card);border:1px solid var(--line);color:var(--fg);padding:7px 10px;border-radius:8px;font-size:13px}
input[type=search]{min-width:220px}
table{width:100%;border-collapse:collapse}th{position:sticky;top:0;text-align:left;color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;padding:10px 12px;border-bottom:1px solid var(--line);background:var(--bg)}
td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr.done{opacity:.42}tr.done .ctx{text-decoration:line-through}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}
.b-repo{background:#16263f;color:var(--repo)}.b-social{background:#2a1d3a;color:var(--social)}
.b-ceiling{background:#332a14;color:var(--warn)}
.b-A{background:#143626;color:var(--ok)}.b-B{background:#2e2a16;color:var(--warn)}.b-C{background:#16263f;color:var(--repo)}
.b-target{background:#143626;color:var(--ok)}.b-unknown{background:#2a2f3a;color:var(--mut)}
.b-yes{background:#143626;color:var(--ok)}.b-no{background:#2a2f3a;color:var(--mut)}
.b-fresh{background:#143626;color:var(--ok)}.b-stale{background:#3a1d1d;color:#f0a0a0}
.who{font-weight:600}.who a{color:var(--fg);text-decoration:none;border-bottom:1px dotted var(--mut)}
.contact a{color:var(--acc);text-decoration:none}.copy{cursor:pointer;color:var(--mut);margin-left:6px;font-size:11px}
.ctx{max-width:430px}.ctx .s{color:var(--mut);font-size:12px}
.notes{width:150px;background:transparent;border:1px solid var(--line);border-radius:6px;color:var(--fg);padding:5px;font-size:12px}
.chk{width:18px;height:18px;cursor:pointer;accent-color:var(--ok)}
.btn{padding:7px 12px;border:1px solid var(--acc);border-radius:8px;background:#1d2738;color:var(--fg);cursor:pointer;font-size:13px}
.muted{color:var(--mut)} .right{margin-left:auto}
</style></head><body>
<header>
 <h1>Lovable-Graduate Targets</h1>
 <div class="sub">Generated __GEN__ · GitHub graduates to email · Ceiling pre-grads to email · Social handles to engage · <span id="savehint">checkmarks &amp; notes save in this browser</span></div>
 <div class="stats">
   <div class="stat"><b id="s-total">0</b><span>showing</span></div>
   <div class="stat"><b id="s-repo">0</b><span>github</span></div>
   <div class="stat"><b id="s-ceiling">0</b><span>ceiling</span></div>
   <div class="stat"><b id="s-social">0</b><span>social</span></div>
   <div class="stat"><b id="s-done" style="color:var(--ok)">0</b><span>messaged</span></div>
 </div>
</header>
<div class="controls">
 <div class="tab on" data-t="all">All</div>
 <div class="tab" data-t="repo">GitHub repos</div>
 <div class="tab" data-t="ceiling">Ceiling (pre-grad)</div>
 <div class="tab" data-t="social">Social</div>
 <input type="search" id="q" placeholder="search name, product, email, quote…">
 <select id="f1"></select><select id="f2"></select>
 <label class="muted"><input type="checkbox" id="hideDone"> hide messaged</label>
 <label class="muted" title="aged-out posts you can no longer usefully engage — HN >2wk · Reddit >2mo · Mastodon/Bluesky >1mo · Dev.to >3mo"><input type="checkbox" id="hideStale" checked> hide stale</label>
 <span id="savemsg" class="muted"></span>
 <button class="btn right" id="export">⬇ Save to worklist</button>
</div>
<table><thead><tr><th></th><th>Type</th><th>Signal</th><th>Who</th><th>Contact</th><th>Context</th><th>Seen</th><th>Notes</th></tr></thead><tbody id="grid"></tbody></table>
<script>
const DATA=__DATA__;
// SERVER mode (served over http) writes edits straight to the worklist CSVs; FILE mode
// (file://) keeps the legacy localStorage + "Save to worklist" export flow.
const SERVER = location.protocol === 'http:' || location.protocol === 'https:';
const LS="lovgrad::";
// rows are handled by DATA index, not id: owner_repo can legitimately exist in BOTH the repo and
// ceiling worklists after a graduation, and an id-keyed map would cross-wire their checkboxes.
// localStorage stays keyed by id, so duplicate-id rows intentionally share tracking state.
function lsget(id){try{return JSON.parse(localStorage.getItem(LS+id))||{}}catch(e){return {}}}
function lsset(id,v){localStorage.setItem(LS+id,JSON.stringify(v))}
DATA.forEach(d=>{const seeded=['contacted','messaged','replied','sent','graduated'].includes((d.seed_status||'').toLowerCase());
  if(SERVER){d._m=seeded; d._n=d.seed_notes||'';}
  else{const cur=lsget(d.id); if(cur.messaged===undefined) lsset(d.id,{messaged:seeded, notes:cur.notes||d.seed_notes||''});}});
function tget(d){return SERVER?{messaged:d._m, notes:d._n||''}:lsget(d.id);}
function flash(t){const m=document.getElementById('savemsg'); if(!m)return; m.textContent=t; if(t==='✓ saved'){setTimeout(()=>{if(m.textContent==='✓ saved')m.textContent='';},1200);}}
function post(d){fetch('/track',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:d.id, messaged:!!d._m, notes:d._n||''})})
  .then(r=>r.json()).then(j=>flash(j.ok?'✓ saved':'⚠ row not found'))
  .catch(()=>flash('⚠ save failed — is the server running?'));}
let tab="all";
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function badge(t,c){return `<span class="badge b-${c}">${esc(t)}</span>`}
function ageLabel(d){d=+d; return d<31?d+'d':Math.round(d/30)+'mo';}
function signal(d){if(d.type==='repo'){return `${badge('Tier '+d.tier,d.tier)} ${badge(d.region,(d.region||'').toLowerCase())} <span class="muted">${esc(d.country)} · ${esc(d.confidence)} · score ${esc(d.score)}</span>`}
  if(d.type==='ceiling'){const tb=d.trend==='increasing'?badge('increasing','fresh'):d.trend==='dropping'?badge('dropping','stale'):d.trend?badge(d.trend,'unknown'):'';
    return `${badge(d.seg_key,d.seg_key)} ${tb} <span class="muted">${esc(d.segment)}</span>`}
  const fb=(d.age_days===''||d.age_days==null)?'':(d.stale?badge('stale '+ageLabel(d.age_days),'stale'):badge(ageLabel(d.age_days),'fresh'));
  return `${badge('score '+d.score,d.score>=4?'A':'B')} ${badge(d.contactable=='True'?'contactable':'monitor',d.contactable=='True'?'yes':'no')} ${fb} <span class="muted">${esc(d.classification)} · ${esc(d.platform)}</span>`}
function contactCell(d){
  if(d.type==='social'){let p=[];
    if(d.post_url)p.push(`<a href="${esc(d.post_url)}" target="_blank">↗ open post</a>`);
    if(d.contact)p.push(`<a href="mailto:${esc(d.contact)}">${esc(d.contact)}</a>`);
    return p.length?`<span class="contact">${p.join(' · ')}</span>`:'<span class="muted">—</span>';}
  if(!d.contact)return '<span class="muted">—</span>';
  if(d.contact_kind==='email')return `<span class="contact"><a href="mailto:${esc(d.contact)}">${esc(d.contact)}</a><span class="copy" onclick="navigator.clipboard.writeText('${esc(d.contact)}')">copy</span></span>`;
  return `<span class="contact"><a href="${esc(d.contact.startsWith('http')?d.contact:'https://'+d.contact)}" target="_blank">${esc(d.contact)}</a></span>`}
function render(){
 const q=document.getElementById('q').value.toLowerCase();
 const f1=document.getElementById('f1').value, f2=document.getElementById('f2').value;
 const hide=document.getElementById('hideDone').checked;
 const hideStale=document.getElementById('hideStale').checked;
 const g=document.getElementById('grid'); g.innerHTML='';
 let nr=0,ns=0,nc=0,nd=0,shown=0;
 DATA.forEach((d,i)=>{
   if(tab!=='all'&&d.type!==tab)return;
   if(q&&!(`${d.name} ${d.context} ${d.contact} ${d.country} ${d.sub}`.toLowerCase().includes(q)))return;
   if(f1&&tab!=='ceiling'&&d.type==='repo'&&d.region!==f1)return;
   if(f1&&tab==='ceiling'&&d.type==='ceiling'&&d.segment!==f1)return;
   if(f2&&d.type==='social'&&d.classification!==f2&&f2!=='')return;
   const st=tget(d); const done=!!st.messaged;
   if(hide&&done)return;
   if(hideStale&&d.type==='social'&&d.stale)return;
   shown++; if(d.type==='repo')nr++; else if(d.type==='ceiling')nc++; else ns++; if(done)nd++;
   const tr=document.createElement('tr'); tr.className=done?'done':'';
   tr.innerHTML=`<td><input class="chk" type="checkbox" ${done?'checked':''} onchange="toggle(${i},this)"></td>
     <td>${badge(d.type==='repo'?'GitHub':d.type==='ceiling'?'Ceiling':'Social',d.type)}</td>
     <td>${signal(d)}</td>
     <td class="who">${d.name_url?`<a href="${esc(d.name_url)}" target="_blank">${esc(d.name)}</a>`:esc(d.name)}</td>
     <td>${contactCell(d)}</td>
     <td class="ctx">${esc(d.context)}<div class="s">${esc(d.sub)}</div></td>
     <td class="muted">${esc(d.first_seen)}</td>
     <td><textarea class="notes" rows="2" onchange="note(${i},this.value)">${esc(st.notes||'')}</textarea></td>`;
   g.appendChild(tr);
 });
 // totals across all messaged (not just shown)
 const totalDone=DATA.filter(d=>tget(d).messaged).length;
 document.getElementById('s-total').textContent=shown;
 document.getElementById('s-repo').textContent=nr;
 document.getElementById('s-ceiling').textContent=nc;
 document.getElementById('s-social').textContent=ns;
 document.getElementById('s-done').textContent=totalDone;
}
function toggle(i,el){const d=DATA[i];
  if(SERVER){d._m=el.checked; post(d);} else {const s=lsget(d.id); s.messaged=el.checked; lsset(d.id,s);}
  render();}
function note(i,v){const d=DATA[i];
  if(SERVER){d._n=v; post(d);} else {const s=lsget(d.id); s.notes=v; lsset(d.id,s);}}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));t.classList.add('on');tab=t.dataset.t;setFilters();render()});
document.getElementById('q').oninput=render;
document.getElementById('hideDone').onchange=render;
document.getElementById('hideStale').onchange=render;
function setFilters(){const f1=document.getElementById('f1'),f2=document.getElementById('f2');
 if(tab==='social'){f1.innerHTML='<option value="">all classes</option>';f1.style.display='none';
   f2.style.display='';f2.innerHTML='<option value="">all classes</option>'+[...new Set(DATA.filter(d=>d.type==='social').map(d=>d.classification))].filter(Boolean).map(c=>`<option>${c}</option>`).join('');}
 else if(tab==='ceiling'){f1.style.display='';
   f1.innerHTML='<option value="">all segments</option>'+[...new Set(DATA.filter(d=>d.type==='ceiling').map(d=>d.segment))].filter(Boolean).map(c=>`<option>${c}</option>`).join('');
   f2.style.display='none';f2.innerHTML='';}
 else{f1.style.display='';f1.innerHTML='<option value="">all regions</option><option>TARGET</option><option>UNKNOWN</option>';f2.style.display='none';f2.innerHTML='';}
 f1.onchange=render;f2.onchange=render;}
document.getElementById('export').onclick=()=>{
 const out={};DATA.forEach(d=>{const s=tget(d);if(s.messaged||s.notes)out[d.id]={messaged:!!s.messaged,notes:s.notes||''}});
 const blob=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='tracking.json';a.click();
 alert('Saved tracking.json to Downloads.\nRun:  python3 pipeline/apply_tracking.py ~/Downloads/tracking.json\nto push it into the worklist CSVs.');};
if(SERVER){document.getElementById('savehint').innerHTML='✓ edits save straight to the worklist CSVs (live)';
  document.getElementById('export').style.display='none';}  // live-saving makes the export redundant
setFilters();render();
</script></body></html>"""

def render_html(rows):
    data_json=json.dumps(rows).replace("</","<\\/")
    n_repo=sum(1 for r in rows if r["type"]=="repo"); n_soc=len(rows)-n_repo
    return TEMPLATE.replace("__DATA__",data_json).replace("__GEN__",datetime.date.today().isoformat())

if __name__=="__main__":
    rows=build_rows()
    with open(os.path.join(ROOT,"outreach","dashboard.html"),"w") as f: f.write(render_html(rows))
    n_repo=sum(1 for r in rows if r["type"]=="repo"); n_ceil=sum(1 for r in rows if r["type"]=="ceiling")
    print(f"wrote outreach/dashboard.html  ({n_repo} github + {n_ceil} ceiling + {len(rows)-n_repo-n_ceil} social = {len(rows)} leads)")
    print("Open it: double-click outreach/dashboard.html  —  or live-edit: python3 pipeline/serve_dashboard.py")

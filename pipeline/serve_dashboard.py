#!/usr/bin/env python3
"""
serve_dashboard.py - run the worklist dashboard as a local server so edits write straight
to the worklist CSVs (no "Save to worklist" / apply_tracking.py step).

  python3 pipeline/serve_dashboard.py        # -> http://127.0.0.1:8787
  python3 pipeline/serve_dashboard.py 9000   # custom port

Routes:
  GET  /        live-rendered dashboard (always reflects the current worklist CSVs)
  GET  /data    current rows as JSON
  POST /track   {id, messaged, notes} -> updates the row's tracking cols in the right CSV

Binds to 127.0.0.1 ONLY - the worklists contain third-party contact PII; never expose this
on a network. Writes are serialized and atomic (write temp + os.replace), and touch only the
tracking columns (status / contacted_on|engaged_on / notes), so a concurrent daily
build_worklist.py run (which preserves those columns) stays safe.
"""
import csv, datetime, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_dashboard as bd

OUT=os.path.join(ROOT,"outreach")
TARGETS=[(os.path.join(OUT,"worklist_repo.csv"),   "owner_repo","contacted_on"),
         (os.path.join(OUT,"worklist_social.csv"), "post_url",  "engaged_on"),
         (os.path.join(OUT,"worklist_ceiling.csv"),"owner_repo","contacted_on")]
LOCK=threading.Lock()

def apply_one(track_id, messaged, notes):
    """Write a single id's tracking into EVERY worklist CSV holding it (worklist_repo and
    worklist_ceiling share the owner_repo key — a graduated builder can appear in both; updating
    all matches keeps the surfaces consistent and matches apply_tracking.py). Returns True if
    found anywhere. messaged -> status='contacted' + date (set once); unchecked -> clears both.
    notes always written when provided. `replied`/`channel` are untouched."""
    found_any=False
    with LOCK:
        for path,key,datecol in TARGETS:
            if not os.path.exists(path): continue
            rows=list(csv.DictReader(open(path)))
            if not rows: continue
            cols=list(rows[0].keys()); found=False
            for r in rows:
                if r.get(key)==track_id:
                    found=True
                    if messaged:
                        if not r.get("status"): r["status"]="contacted"
                        if not r.get(datecol): r[datecol]=datetime.date.today().isoformat()
                    else:
                        r["status"]=""; r[datecol]=""
                    if notes is not None: r["notes"]=notes
            if found:
                tmp=path+".tmp"
                with open(tmp,"w",newline="") as f:
                    w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)
                os.replace(tmp,path)
                found_any=True
    return found_any

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b=body.encode("utf-8") if isinstance(body,str) else body
        self.send_response(code); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/","/index.html"):
            self._send(200, bd.render_html(bd.build_rows()))
        elif self.path=="/data":
            self._send(200, json.dumps(bd.build_rows()), "application/json")
        elif self.path=="/favicon.ico":
            self._send(204, b"")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path!="/track":
            return self._send(404, "not found", "text/plain")
        n=int(self.headers.get("Content-Length") or 0)
        try:
            payload=json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"ok":False,"err":"bad json"}), "application/json")
        tid=payload.get("id")
        if not tid:
            return self._send(400, json.dumps({"ok":False,"err":"no id"}), "application/json")
        try:
            ok=apply_one(tid, bool(payload.get("messaged")), payload.get("notes"))
        except Exception as e:
            return self._send(500, json.dumps({"ok":False,"err":str(e)}), "application/json")
        self._send(200 if ok else 404, json.dumps({"ok":ok}), "application/json")

    def log_message(self, *a):  # keep the console quiet
        pass

if __name__=="__main__":
    port=int(sys.argv[1]) if len(sys.argv)>1 else 8787
    srv=ThreadingHTTPServer(("127.0.0.1",port), Handler)
    print(f"dashboard -> http://127.0.0.1:{port}   (Ctrl-C to stop)")
    print("edits (checkmarks + notes) save live to outreach/worklist_*.csv")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")

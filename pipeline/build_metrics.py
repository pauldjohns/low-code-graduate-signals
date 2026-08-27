#!/usr/bin/env python3
"""build_metrics.py - conversion dashboard for the outreach engine.

Renders outreach/metrics.html: per-segment sends, replies, and product signups, with
conversion rates, plus a daily send timeline. Sends come from send_log.csv (has a segment column);
replies + account-creations come from a gmail.readonly scan of the sender mailbox, matched to sent
recipients. Degrades cleanly to sends-only if gmail.readonly isn't granted.

  python3 pipeline/build_metrics.py [--days N] [--no-gmail]
Wired into run_chain.sh (throttled ~daily). View: open outreach/metrics.html, or serve it.
"""
import argparse, csv, json, os, re, sys, html
from datetime import datetime, date
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
SEND = os.path.join(ROOT, "outreach", "send")
SEND_LOG = os.path.join(SEND, "send_log.csv")
WORKLIST = os.path.join(ROOT, "outreach", "worklist_ceiling.csv")
CONFIG = os.path.join(SEND, "config.json")
BOUNCES = os.path.join(SEND, "bounces.csv")
SUPPRESSION = os.path.join(SEND, "suppression.csv")
OUT_HTML = os.path.join(ROOT, "outreach", "metrics.html")
OUT_JSON = os.path.join(SEND, "metrics.json")
READONLY = "https://www.googleapis.com/auth/gmail.readonly"
ADDR_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# signup notification — the "account created" conversion signal (see cohort-tracker memory)
ACCOUNT_SUBJECT = "New org created"


def norm(e): return (e or "").strip().lower().strip("<>")

def read_csv(p): return list(csv.DictReader(open(p))) if os.path.exists(p) else []

def from_address():
    try: return norm(json.load(open(CONFIG)).get("from_address"))
    except Exception: return ""

# ---------------- sends + attribution identity (local, authoritative) ----------------
def _slug(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def load_sends():
    """Returns (sent, repos, notes_signup, daily).
      sent: {email: {segment, ts}} — for sent + reply counts (dedup => one live send per email/run).
      repos: {owner_repo: {segment, emails:set, handles:set}} — attribution identity so a signup
        counts even when the person used a DIFFERENT email than we cold-emailed (match the repo
        owner's handle or any commit/contact email of the repo we sent to).
      notes_signup: {owner_repo} flagged as a signup in the worklist notes — human-verified
        product-name matches that no email/handle link can catch (e.g. CleanCo)."""
    rows = [r for r in read_csv(SEND_LOG) if r.get("mode") == "live"]
    sent = {}; repos = {}; daily = Counter()
    for r in rows:
        e = norm(r.get("to")); repo = r.get("owner_repo", ""); ts = r.get("ts", "")
        if not e: continue
        daily[ts[:10]] += 1
        if e not in sent or ts < sent[e]["ts"]:
            sent[e] = {"segment": r.get("segment") or "?", "ts": ts}
        if repo:
            d = repos.setdefault(repo, {"segment": r.get("segment") or "?", "emails": set(), "handles": set()})
            d["emails"].add(e); d["handles"].add(_slug(e.split("@")[0])); d["handles"].add(_slug(repo.split("/")[0]))
    notes_signup = set()
    for r in read_csv(WORKLIST):
        repo = r.get("owner_repo", "")
        if repo not in repos: continue
        for x in ADDR_RE.findall((r.get("contact_paths", "") or "") + " " + (r.get("email", "") or "")):
            repos[repo]["emails"].add(norm(x))
        if re.search(r"signed up|likely signup|possible signup|new org|else org", (r.get("notes") or ""), re.I):
            notes_signup.add(repo)
    return sent, repos, notes_signup, daily

# ---------------- deliverability: bounces + suppression ----------------
def deliverability():
    """Hard-bounce rate and suppression totals for THIS campaign.

    Added 2026-07-27, when `breaker_enabled` and `adaptive_throttle` were both turned off and
    pre-send Bouncer verification became the only bounce control. Bounce rate stopped being
    something the engine reacts to on its own, so it has to be something a human can see.

    The rate is computed by INTERSECTING bounces.csv with our own live send log — never by counting
    rows in bounces.csv. Both engines send from sender@example.com and bounce_scan reads that one
    mailbox, so it cannot tell whose DSN it found: on 2026-07-27 a `--days 10` scan attributed 10
    the sibling campaign bounces to this campaign. Intersecting drops them automatically, and
    they are reported separately as `foreign` rather than silently discarded.

    Two windows: trailing `throttle_window` live sends (what the throttle used to act on, so the
    number stays comparable) and all-time.
    """
    hard = {norm(b.get("email")) for b in read_csv(BOUNCES)
            if (b.get("type") or "hard") == "hard" and norm(b.get("email"))}
    live = [norm(r.get("to")) for r in read_csv(SEND_LOG) if r.get("mode") == "live" and r.get("to")]
    ours = hard & set(live)
    try:
        cfg = json.load(open(CONFIG))
    except Exception:
        cfg = {}
    win_n = int(cfg.get("throttle_window", 100) or 100)
    win = live[-win_n:]
    win_b = sum(1 for e in win if e in hard)

    supp = read_csv(SUPPRESSION)
    by_reason = Counter((r.get("reason") or "unknown").strip() for r in supp)

    # Bounce observation lags sending: bounce_scan runs inside the ~hourly poll block, and its
    # default window is 2 days. Sends newer than the last scan have not been observed at all, so
    # the rate below is a FLOOR. Surface that rather than implying the number is settled.
    last_scan = None
    try:
        last_scan = datetime.fromtimestamp(os.path.getmtime(os.path.join(SEND, ".last_poll")))
    except OSError:
        pass
    newest_send = max((r.get("ts", "") for r in read_csv(SEND_LOG) if r.get("mode") == "live"),
                      default="")
    unobserved = 0
    if last_scan and newest_send:
        cutoff = last_scan.strftime("%Y-%m-%dT%H:%M:%S")
        unobserved = sum(1 for r in read_csv(SEND_LOG)
                         if r.get("mode") == "live" and (r.get("ts") or "") > cutoff)

    return {"hard_ours": len(ours), "hard_foreign": len(hard) - len(ours),
            "window_n": len(win), "window_bounces": win_b,
            "window_rate": win_b / len(win) if win else 0.0,
            "alltime_sent": len(live), "alltime_rate": len(ours) / len(live) if live else 0.0,
            "suppressed": len(supp), "by_reason": dict(by_reason.most_common()),
            "last_scan": last_scan.strftime("%Y-%m-%d %H:%M") if last_scan else "",
            "unobserved_sends": unobserved,
            "breaker_on": cfg.get("breaker_enabled", True) is not False,
            "throttle_on": cfg.get("adaptive_throttle", True) is True,
            "verify_on": cfg.get("verify_enabled") is True,
            "require_verified": cfg.get("require_verified") is True}


# ---------------- gmail: replies + account-creations ----------------
def _token_has_readonly(gmail_auth):
    try: return READONLY in json.load(open(gmail_auth.TOKEN)).get("scopes", [])
    except Exception: return False

def _headers(full): return {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}

def _decode(payload):
    import base64
    out = []
    def walk(p):
        b = p.get("body", {}).get("data")
        if b:
            try: out.append(base64.urlsafe_b64decode(b).decode("utf-8", "ignore"))
            except Exception: pass
        for part in p.get("parts", []) or []: walk(part)
    walk(payload)
    return "\n".join(out)

def gmail_signals(repos, sent_emails, days):
    """(replied:set[email], acct_repos:set[owner_repo], total_accounts:int, ok, note). Each 'New org
    created' signup is matched to a sent repo by creator email OR owner-handle. Never raises."""
    try:
        import gmail_auth
        if not _token_has_readonly(gmail_auth):
            return set(), set(), 0, False, "gmail.readonly not granted (run pipeline/go.sh) — sends-only view."
        import warnings; warnings.filterwarnings("ignore")
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=gmail_auth._creds(), cache_discovery=False)
        me = from_address()
        DAEMON = ("mailer-daemon", "postmaster", "mail delivery")
        email_to_repo = {}; handle_to_repo = {}         # reverse indices for attribution
        for repo, d in repos.items():
            for e in d["emails"]: email_to_repo.setdefault(e, repo)
            for h in d["handles"]:
                if h: handle_to_repo.setdefault(h, repo)
        # replies: inbound from a sent recipient (reply usually comes from the address we emailed)
        replied = set()
        for m in svc.users().messages().list(userId="me", q=f"newer_than:{days}d in:inbox",
                                              maxResults=500).execute().get("messages", []):
            full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                              metadataHeaders=["From"]).execute()
            frm = _headers(full).get("from", "").lower()
            if any(h in frm for h in DAEMON): continue
            a = ADDR_RE.search(frm)
            r = norm(a.group(0)) if a else ""
            if r and r != me and r in sent_emails: replied.add(r)
        # account-creations: "New org created" -> match creator email/handle to a sent repo
        acct_repos = set(); total_accounts = 0
        for m in svc.users().messages().list(userId="me", q=f'newer_than:{days}d subject:("{ACCOUNT_SUBJECT}")',
                                             maxResults=500).execute().get("messages", []):
            full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            total_accounts += 1
            body = _decode(full.get("payload", {})) + " " + full.get("snippet", "")
            for c in [norm(x) for x in ADDR_RE.findall(body) if not x.lower().endswith(OWN_DOMAIN)]:
                repo = email_to_repo.get(c) or handle_to_repo.get(_slug(c.split("@")[0]))
                if repo: acct_repos.add(repo); break
        return replied, acct_repos, total_accounts, True, ""
    except Exception as e:
        return set(), set(), 0, False, f"gmail scan failed: {str(e)[:80]}"

# ---------------- aggregate ----------------
def build(days, use_gmail):
    sent, repos, notes_signup, daily = load_sends()
    sent_emails = set(sent)
    replied = set(); acct_repos = set(); total_accounts = 0; gmail_ok = False
    note = "gmail scan skipped (--no-gmail)."
    if use_gmail and sent_emails:
        replied, acct_repos, total_accounts, gmail_ok, note = gmail_signals(repos, sent_emails, days)
    # a repo is "converted" if a signup matched it (email/handle) OR the worklist notes flag it
    # (human-verified product-name match). Attribute each converted repo to its send segment.
    account_repos = acct_repos | notes_signup
    repo_seg = {r: d["segment"] for r, d in repos.items()}

    segs = sorted({v["segment"] for v in sent.values()} | {repo_seg.get(r, "?") for r in account_repos})
    per = {}
    for s in segs:
        se = {e for e, v in sent.items() if v["segment"] == s}
        rep = len({e for e in replied if sent.get(e, {}).get("segment") == s})
        acc = len({r for r in account_repos if repo_seg.get(r) == s})
        per[s] = {"sent": len(se), "replied": rep, "accounts": acc,
                  "reply_rate": rep / len(se) if se else 0, "acct_rate": acc / len(se) if se else 0}
    ts_sent = len(sent_emails); ts_rep = len(replied); ts_acc = len(account_repos)
    tot = {"sent": ts_sent, "replied": ts_rep, "accounts": ts_acc,
           "reply_rate": ts_rep / ts_sent if ts_sent else 0,
           "acct_rate": ts_acc / ts_sent if ts_sent else 0, "total_accounts": total_accounts}
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "window_days": days, "gmail_ok": gmail_ok, "note": note,
            "per_segment": per, "totals": tot, "daily": dict(sorted(daily.items())),
            "deliverability": deliverability()}

# ---------------- render ----------------
def _pct(x): return f"{x*100:.0f}%"
def _pct1(x): return f"{x*100:.1f}%"
def _bounce_cls(rate):
    """Cold-outreach norms cited in method/AUTOSEND.md: <2% safe, 2-5% watch, >5% stop."""
    return "ok" if rate < 0.02 else ("watch" if rate < 0.05 else "bad")
def _bar(v, mx, cls):
    w = 0 if not mx else max(2, round(v / mx * 100))
    return f'<div class="bar {cls}" style="width:{w}%"></div>'

def render(mx):
    per, tot, daily = mx["per_segment"], mx["totals"], mx["daily"]
    order = ["B_all_bot", "A_hybrid", "C_mover", "C_mover_fresh", "presumed_silent_graduate"]
    segs = sorted(per, key=lambda s: (order.index(s) if s in order else 9, s))
    rows = ""
    for s in segs:
        p = per[s]
        rows += (f'<tr><td class="seg">{html.escape(s)}</td>'
                 f'<td class="num">{p["sent"]}</td>'
                 f'<td class="num">{p["replied"]}</td><td class="rate">{_pct(p["reply_rate"])}</td>'
                 f'<td class="num">{p["accounts"]}</td><td class="rate">{_pct(p["acct_rate"])}</td></tr>')
    maxd = max(daily.values()) if daily else 0
    days_html = "".join(
        f'<div class="drow"><span class="dlabel">{d}</span>'
        f'<span class="dbarwrap">{_bar(n, maxd, "sent")}</span><span class="dnum">{n}</span></div>'
        for d, n in list(daily.items())[-21:])
    gwarn = "" if mx["gmail_ok"] else f'<div class="warn">⚠ {html.escape(mx["note"])} Reply/account numbers unavailable or partial.</div>'
    acct_ctx = f' · {tot["total_accounts"]} total signups in window' if mx["gmail_ok"] else ""
    return f"""<div class="wrap">
<header><h1>Outreach conversions</h1>
<div class="meta">generated {html.escape(mx["generated"])} · {mx["window_days"]}-day reply/account window{html.escape(acct_ctx)}</div></header>
{gwarn}
<section class="tiles">
  <div class="tile"><div class="tk">Sent</div><div class="tv">{tot["sent"]}</div><div class="ts">unique recipients</div></div>
  <div class="tile"><div class="tk">Replies</div><div class="tv">{tot["replied"]}</div><div class="ts">{_pct(tot["reply_rate"])} reply rate</div></div>
  <div class="tile"><div class="tk">Accounts created</div><div class="tv">{tot["accounts"]}</div><div class="ts">{_pct(tot["acct_rate"])} of sent</div></div>
</section>
<h2>By segment</h2>
<table><thead><tr><th>Segment</th><th>Sent</th><th>Replies</th><th>Reply&nbsp;%</th><th>Accounts</th><th>Acct&nbsp;%</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Deliverability</h2>
{_deliverability_html(mx.get("deliverability") or {})}
<h2>Sends per day</h2>
<div class="daily">{days_html}</div>
</div>"""


def _deliverability_html(d):
    if not d:
        return ""
    cls = _bounce_cls(d["window_rate"])
    reasons = " · ".join(f"{html.escape(k)} {v}" for k, v in (d.get("by_reason") or {}).items()) or "—"
    # What is actually stopping a bad list right now. With the breaker and throttle both off this is
    # the only place that posture is visible outside chain.log, so it is stated, not implied.
    brakes = []
    brakes.append("breaker ON" if d.get("breaker_on") else "breaker OFF")
    brakes.append("throttle ON" if d.get("throttle_on") else "throttle OFF")
    if d.get("verify_on"):
        brakes.append("pre-send verification ON" + (" (gating)" if d.get("require_verified") else " (recording only)"))
    else:
        brakes.append("pre-send verification OFF")
    no_auto = not d.get("breaker_on") and not d.get("throttle_on")
    posture = (f'<div class="{"warn" if no_auto else "note"}">'
               f'{"⚠ No automatic brake on bounce rate — " if no_auto else ""}'
               f'{html.escape(" · ".join(brakes))}.'
               f'{" STOP is the manual kill switch." if no_auto else ""}</div>')
    stale = ""
    if d.get("unobserved_sends"):
        stale = (f'<div class="note">↻ {d["unobserved_sends"]} send(s) newer than the last bounce scan'
                 f'{" (" + html.escape(d["last_scan"]) + ")" if d.get("last_scan") else ""} — '
                 f'not yet observed, so the rate below is a floor.</div>')
    foreign = ""
    if d.get("hard_foreign"):
        foreign = (f'<div class="note">{d["hard_foreign"]} hard bounce(s) in bounces.csv belong to '
                   f'another campaign sharing this mailbox and are excluded from these rates.</div>')
    return f"""{posture}{stale}{foreign}
<section class="tiles">
  <div class="tile"><div class="tk">Bounce rate</div>
    <div class="tv {cls}">{_pct1(d["window_rate"])}</div>
    <div class="ts">{d["window_bounces"]}/{d["window_n"]} most recent sends</div></div>
  <div class="tile"><div class="tk">All-time bounces</div>
    <div class="tv">{d["hard_ours"]}</div>
    <div class="ts">{_pct1(d["alltime_rate"])} of {d["alltime_sent"]} sends</div></div>
  <div class="tile"><div class="tk">Suppressed</div>
    <div class="tv">{d["suppressed"]}</div>
    <div class="ts">{reasons}</div></div>
</section>"""

CSS = """
:root{--bg:#fbfbfa;--fg:#1c1c1a;--muted:#75756e;--card:#fff;--line:#e7e6e1;--accent:#3b6ea5;--warn:#8a6d1a;--warnbg:#fdf6e3;--good:#2f7a4d;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#e9e9e6;--muted:#9a9a92;--card:#212127;--line:#2f2f36;--accent:#6fa8dc;--warn:#d8c27a;--warnbg:#2a2410;--good:#6fbf8b;--bad:#f2756a}}
:root[data-theme=light]{--bg:#fbfbfa;--fg:#1c1c1a;--muted:#75756e;--card:#fff;--line:#e7e6e1;--accent:#3b6ea5;--warn:#8a6d1a;--warnbg:#fdf6e3;--good:#2f7a4d;--bad:#b3261e}
:root[data-theme=dark]{--bg:#17171a;--fg:#e9e9e6;--muted:#9a9a92;--card:#212127;--line:#2f2f36;--accent:#6fa8dc;--warn:#d8c27a;--warnbg:#2a2410;--good:#6fbf8b;--bad:#f2756a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px}
header h1{margin:0 0 4px;font-size:24px}.meta{color:var(--muted);font-size:13px}
.warn{background:var(--warnbg);color:var(--warn);border:1px solid var(--line);padding:10px 12px;border-radius:8px;margin:16px 0;font-size:13px}
.tiles{display:flex;gap:14px;margin:22px 0}
.tile{flex:1;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.tk{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tv{font-size:30px;font-weight:650;margin:4px 0}.ts{color:var(--muted);font-size:12px}
h2{font-size:15px;margin:26px 0 10px;color:var(--muted);font-weight:600}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:10px 12px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td.seg{text-align:left}th{font-size:12px;color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:none}.seg{font-family:ui-monospace,monospace;font-size:13px}
.num{font-variant-numeric:tabular-nums}.rate{color:var(--accent);font-variant-numeric:tabular-nums}
.daily{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.drow{display:flex;align-items:center;gap:10px;padding:2px 0}
.dlabel{width:82px;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
.dbarwrap{flex:1}.bar{height:12px;border-radius:3px}.bar.sent{background:var(--accent)}
.dnum{width:34px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums}
.note{background:var(--card);border:1px solid var(--line);color:var(--muted);padding:9px 12px;border-radius:8px;margin:10px 0;font-size:13px}
.tv.ok{color:var(--good)}.tv.watch{color:var(--warn)}.tv.bad{color:var(--bad)}
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-gmail", action="store_true")
    a = ap.parse_args()
    mx = build(a.days, use_gmail=not a.no_gmail)
    json.dump(mx, open(OUT_JSON, "w"), indent=1)
    doc = f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Outreach conversions</title><style>{CSS}</style></head><body>{render(mx)}</body></html>"
    with open(OUT_HTML, "w") as f: f.write(doc)
    t = mx["totals"]; d = mx["deliverability"]
    print(f"[metrics] {t['sent']} sent · {t['replied']} replies ({_pct(t['reply_rate'])}) · "
          f"{t['accounts']} accounts ({_pct(t['acct_rate'])}) · "
          f"bounce {_pct1(d['window_rate'])} ({d['window_bounces']}/{d['window_n']}) · "
          f"{d['suppressed']} suppressed · gmail_ok={mx['gmail_ok']} -> {os.path.relpath(OUT_HTML, ROOT)}")

if __name__ == "__main__":
    main()

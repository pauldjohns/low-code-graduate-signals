#!/usr/bin/env python3
"""
send_outreach.py - staggered outreach sender for the ceiling worklist.

Selects sendable rows -> validates -> renders the template -> sends via Gmail ->
records. Ships in DRY-RUN (config dry_run:true): renders every message to
outreach/send/dryrun/ and sends NOTHING. Design + rationale: method/AUTOSEND.md.

Going live requires THREE deliberate config acts, not one: dry_run:false AND
template_approved:true AND real non-empty copy (no [PLACEHOLDER] block).

Safety (hardened after review):
- exclusive flock: overlapping runs (launchd fires while a prior run is mid-send)
  can't both send the same rows.
- write-ahead send_log: a 'pending' row is written BEFORE the Gmail call and a
  'live' row after, so a crash in between can never cause a resend (dedup treats
  pending as sent; startup warns on any un-reconciled pending).
- STOP kill-switch + HALT (breaker) block; daily cap AND send-window are
  re-checked before every send; per-send jitter.

  python3 pipeline/send_outreach.py                 # one pass (dry-run unless config live)
  python3 pipeline/send_outreach.py --limit 5       # cap this pass (small first live batch)
  python3 pipeline/send_outreach.py --config path
"""
import argparse, csv, fcntl, hashlib, json, os, random, re, sys, time
from datetime import datetime, timezone, time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
SEND = os.path.join(ROOT, "outreach", "send")
STOP = os.path.join(SEND, "STOP"); HALT = os.path.join(SEND, "HALT")
LOCK = os.path.join(SEND, ".send.lock")
SEND_LOG = os.path.join(SEND, "send_log.csv")
BOUNCES = os.path.join(SEND, "bounces.csv")
SUPPRESSION = os.path.join(SEND, "suppression.csv")
DRYRUN = os.path.join(SEND, "dryrun")
LOG_COLS = ["ts", "to", "owner_repo", "segment", "subject", "message_id", "run_id", "mode"]
GENERIC_LOCALS = {"info", "sales", "support", "hello", "contact", "admin", "team", "office",
                  "mail", "help", "noreply", "service", "billing", "careers", "jobs", "press"}
ROLE_LOCALS = GENERIC_LOCALS | {"hi", "hey", "accounts", "enquiries", "inquiries", "hola",
                                "contacto", "kontakt", "founders", "general", "no-reply", "reception",
                                # PT/ES/DE/FR role locals (leads span BR/LatAm/EU)
                                "contato", "atendimento", "comercial", "vendas", "ventas", "soporte",
                                "suporte", "kontakt", "contact", "bonjour", "empresa", "geral"} | {
                                # scraped-contact-page role inboxes (bounce-prone). Added 2026-07-17
                                # after the hard-bounce breaker tripped on seo@/agent@ — --scrape-sites
                                # pulls these off footers/contact pages. is_role also matches on the
                                # first "._-"-delimited token, so "seo.team"/"marketing-eu" are caught.
                                "seo", "agent", "agents", "marketing", "webmaster", "postmaster",
                                "hostmaster", "abuse", "newsletter", "notifications", "notification",
                                "do-not-reply", "donotreply", "hr", "recruiting", "recruitment",
                                "legal", "privacy", "dpo", "compliance", "finance", "accounting",
                                "invoicing", "invoices", "invoice", "payments", "orders", "order",
                                "booking", "bookings", "reservations", "reservation", "partnerships",
                                "partners", "feedback", "returns", "refunds", "shop", "store",
                                "ecommerce", "security", "sysadmin", "mailer", "mailer-daemon",
                                "bounce", "bounces", "welcome", "kundenservice", "vertrieb",
                                "servicio", "informacion", "informazioni", "ufficio"} | {
                                # missed by the 07-17 pass: community@ hard-bounced 07-18, ai@ and
                                # dev@ were sent in error the same morning. Department inboxes below
                                # came out of the 07-18 queue audit.
                                "community", "ai", "dev", "devs", "developer", "developers",
                                "devops", "nurse", "desenvolvimento", "falecom", "itcenter"}
# Machine identities: git AUTHOR addresses belonging to coding agents and CI, not people. The
# target population is builders who use AI coding tools, so their commit history is increasingly
# authored by the tool rather than by them — agent@antigravity.ai hard-bounced on 07-17, and the
# 07-18 audit found codex@openai.com queued twice plus fix@claude.ai, all sendable.
BOT_LOCALS = {"bot", "bots", "ci", "cd", "codex", "commit", "commits", "devin", "dependabot",
              "renovate", "actions", "github-actions", "jenkins", "travis", "circleci",
              "automation", "automated", "robot", "daemon", "semantic-release",
              # antigravity@google.com hard-bounced 07-18 (helped trip the breaker at 7/100).
              # An agent whose vendor is a big company signs commits on the EMPLOYER domain, so
              # the product domain in BOT_DOMAINS does not catch it and must not be widened to
              # google.com. Match the agent's own name as a local instead.
              "antigravity"}
# Domains where ANY address is a tool identity rather than a person. Deliberately product/bot
# domains only — never an employer domain (a real OpenAI employee is @openai.com and is a valid
# lead; the Codex agent is caught by the "codex" local above, not by blocking the domain).
BOT_DOMAINS = {"claude.ai", "antigravity.ai", "cursor.sh", "devin.ai", "gpteng.co", "lovable.dev",
               "lovable.app", "users.noreply.github.com", "dependabot.com", "renovatebot.com"}
MERGE_RE = re.compile(r"\{\{(\w+)\}\}")
MIN_BODY = 20  # a live body shorter than this is almost certainly unfinished


def norm(email):
    return (email or "").strip().lower()

def load_json(p):
    with open(p) as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}

def read_csv(p):
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []

def load_suppression():
    return {norm(r.get("email")) for r in read_csv(SUPPRESSION) if r.get("email")}

def append_log(row):
    new = not os.path.exists(SEND_LOG)
    with open(SEND_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS, extrasaction="ignore")
        if new: w.writeheader()
        w.writerow(row); f.flush(); os.fsync(f.fileno())

# ---------- auth-failure handling ----------
# A dead/revoked OAuth token (invalid_grant) is RUN-FATAL, not a per-recipient error: the send is
# rejected at auth BEFORE Gmail accepts anything (provably not delivered), and every remaining send
# this run fails identically. So on the first one we roll back the in-flight write-ahead pending
# (the message never left) and abort the run — instead of burning the rest of the batch into
# permanent 'pending' skips (the 2026-07-16 outage: one dead token parked 27 leads as sent).
def _auth_fatal(e):
    """True for a non-transient OAuth failure (token expired/revoked). Transient/ambiguous errors
    (timeout, 5xx, connection reset) return False and stay treated-as-sent per the write-ahead log."""
    try:
        from google.auth.exceptions import RefreshError
        if isinstance(e, RefreshError):
            return True
    except Exception:
        pass
    return "invalid_grant" in str(e)

def _write_halt(run_id, msg):
    with open(HALT, "w") as f:
        f.write(f"{run_id} auth failure: {msg}")

def _rollback_pending(to, run_id):
    """Delete the write-ahead 'pending' row just written for (to, run_id). Safe ONLY on a
    provably-not-delivered auth failure (the message never left). Caller holds the send flock."""
    rows = read_csv(SEND_LOG)
    keep = [r for r in rows if not (norm(r.get("to")) == norm(to)
                                    and r.get("run_id") == run_id and r.get("mode") == "pending")]
    if len(keep) == len(rows):
        return
    tmp = SEND_LOG + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS, extrasaction="ignore")
        w.writeheader(); w.writerows(keep); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, SEND_LOG)

# ---------- template ----------
def load_template(path):
    raw = open(os.path.join(ROOT, path) if not os.path.isabs(path) else path).read().splitlines()
    subject, body_start = "", 0
    for i, line in enumerate(raw):
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip(); body_start = i + 1; break
    body = "\n".join(raw[body_start:]).lstrip("\n")
    return subject, body

def first_name(row):
    local = norm(row.get("email")).split("@")[0]
    if any(c.isdigit() for c in local):          # digits -> not a clean name, don't guess
        return "there"
    toks = [t for t in re.split(r"[^a-zA-Z]", local) if len(t) >= 3]
    if len(toks) == 1 and toks[0].lower() not in GENERIC_LOCALS and 3 <= len(toks[0]) <= 15:
        return toks[0].capitalize()
    return "there"

def app_name(repo):
    """Human-readable app name from a Lovable repo slug: strip trailing hashes and
    'remix-of-' prefixes, hyphens->spaces, title-case. 'yum-run-express-bfb6c1ed' -> 'Yum Run
    Express'; 'note-worthy-scribe-app' -> 'Note Worthy Scribe App'. Falls back to the raw slug."""
    slug = repo.split("/")[-1] if repo else ""
    n = re.sub(r"-[0-9a-f]{6,}$", "", slug)
    n = re.sub(r"^(?:remix-of-)+", "", n)
    n = re.sub(r"[-_]+", " ", n).strip()
    return n.title() if n else slug

def render(tmpl, row):
    repo = row.get("owner_repo", "")
    fields = {
        "first_name": first_name(row),
        "app_name": app_name(repo),
        "repo": repo,
        "repo_name": repo.split("/")[-1] if repo else "",
        "product": row.get("product", ""),
        "live_url": row.get("live_url", ""),
        "signal": row.get("signal_deps", ""),   # NOTE: may carry a scan-internal "edge:host" prefix
    }                                            # (ceiling_poll edge detection) — don't surface {{signal}} verbatim in copy
    # single pass: a field value containing {{x}} is NOT re-expanded
    return MERGE_RE.sub(lambda m: (fields.get(m.group(1)) or ""), tmpl)

# ---------- circuit breaker ----------
def breaker_reason(cfg, live_log):
    # the operator disabled the automatic halt 2026-07-27, when Bouncer mailbox verification was wired in
    # ahead of the send (method/VERIFY-INTEGRATION.md). The breaker was a post-hoc instrument for a
    # problem now addressed pre-send: it cannot tell a bad list from an unlucky window, it deadlocks
    # by construction (breaker_reason runs BEFORE any send, so while halted the trailing window is
    # frozen and no clean send can ever dilute it), and clearing HALT re-tripped repeatedly.
    # Bounces are still SCANNED and hard-bounced addresses are still SUPPRESSED, so nobody is
    # re-mailed and the rate stays visible in bounces.csv and on the dashboard. What replaces the
    # halt is throttle_factor() below: bounce rate scales VOLUME instead of flipping a switch.
    # With this false, nothing halts on BOUNCE RATE. Other automatic stops remain: HALT (written by
    # the auth-fatal guard and by the throttle's stop tier), the daily cap, the per-recipient
    # timezone window, suppression/dedup, and the role/region/segment filters.
    if cfg.get("breaker_enabled", True) is False:
        return None
    hard = {e for e in (norm(b.get("email")) for b in read_csv(BOUNCES)
                        if (b.get("type") or "hard") == "hard") if e}
    if not hard or not live_log:
        return None
    win = live_log[-max(1, int(cfg["bounce_window"] or 100)):]   # [-0:] would be the WHOLE log
    wb = sum(1 for r in win if norm(r.get("to")) in hard)
    if len(win) >= 10 and wb / len(win) >= float(cfg["bounce_rate_halt"]):
        return f"hard-bounce {wb}/{len(win)} = {wb/len(win):.0%} >= {float(cfg['bounce_rate_halt']):.0%}"
    burst_n, burst_win = cfg["bounce_burst"]
    recent = live_log[-int(burst_win):]
    rb = sum(1 for r in recent if norm(r.get("to")) in hard)
    if rb >= int(burst_n):
        return f"{rb} hard bounces in last {len(recent)} sends (>= {burst_n})"
    return None

# ---------- adaptive throttle ----------
# What replaces the binary breaker. The breaker was the wrong instrument: it flipped a switch on a
# trailing rate whose confidence interval was far wider than the gap between "fine" and "stop"
# (7/100 with a 6% threshold), stopped everything, and taught us nothing. Bounce rate should
# modulate VOLUME. A clean run earns its way up to the full cap; a dirty one is throttled back
# while still producing data; only a genuinely dangerous rate stops sending. Reputation damage
# scales with volume, so halving the cap halves the harm without ending the campaign.
#
# This is the SECOND line of defence. The first is now pre-send: pipeline/verify_queue.py culls
# undeliverable mailboxes before they are ever mailed (method/VERIFY-INTEGRATION.md). The throttle
# exists for what verification cannot catch — silent-quarantine gateways, mailboxes that die
# between verification and send, and Gmail's habit of returning 250 for every address.
#
# Tiers are (min_bounce_rate, cap_multiplier), evaluated against the trailing window, and only once
# the window holds throttle_min_n sends. Below that the denominator cannot support a decision and
# the cap stays full.
THROTTLE_TIERS = [(0.20, 0.0),    # >=20%: stop. the list is bad, not unlucky.
                  (0.12, 0.25),   # >=12%: quarter speed
                  (0.07, 0.50),   # >=7%:  half speed
                  (0.04, 0.75)]   # >=4%:  three-quarter speed
THROTTLE_MIN_N = 40               # below this the denominator cannot support a decision

def throttle_factor(cfg, live_log):
    """(multiplier, note). Multiplier scales today's cap by the trailing hard-bounce rate.

    Always returns a non-empty note: with the breaker off this is the only bounce-driven control,
    so "it is switched off" must be as loud in the log as "it is at full speed". A silent disable
    plus breaker_enabled:false would leave a live cold sender with no bounce control and no line
    in chain.log saying so.
    """
    if cfg.get("adaptive_throttle", True) is not True:
        return 1.0, "DISABLED (adaptive_throttle is not literally true) — no bounce-driven control"
    # falsy emails would match every send whose `to` is blank; drop them before they poison the set
    hard = {e for e in (norm(b.get("email")) for b in read_csv(BOUNCES)
                        if (b.get("type") or "hard") == "hard") if e}
    win = live_log[-max(1, int(cfg.get("throttle_window", 100) or 100)):]   # [-0:] would be the WHOLE log
    n = len(win)
    if not hard:
        return 1.0, f"no hard bounces on record over last {n} -> full speed"
    if n < max(1, int(cfg.get("throttle_min_n", THROTTLE_MIN_N))):   # max(1,…): n==0 would divide by zero
        return 1.0, f"bounce sample too small to act on ({n} sends)"
    rate = sum(1 for r in win if norm(r.get("to")) in hard) / n
    for threshold, mult in THROTTLE_TIERS:
        if rate >= threshold:
            return mult, f"bounce {rate:.0%} over last {n} -> cap x{mult}"
    return 1.0, f"bounce {rate:.0%} over last {n} -> full speed"

# ---------- selection ----------
def in_window(cfg, now):
    hm = now.strftime("%H:%M")
    return any(a <= hm <= b for a, b in cfg["send_windows"])

def is_role(email):
    """Shared inbox (sales@/info@/support@ ...) or a machine identity (codex@/ci@/fix@claude.ai) —
    either way not a person who wants a personal note. Matched on the WHOLE local: a first-token
    rule lived here until 2026-07-18 and, across the project's entire 661-address history, caught
    0 role inboxes and 2 real people (mail.to.sample@, contato.sampledev@). Widen the sets
    below, never the match."""
    e = norm(email)
    local, _, domain = e.partition("@")
    return (local in ROLE_LOCALS or local in BOT_LOCALS or domain in BOT_DOMAINS
            or "noreply" in local or "no-reply" in local)

# Region gate: target = US/Canada/UK/Europe (project definition). Only positively NON-target rows
# are dropped; unknown is kept (the operator's call). No region pass exists on the ceiling stream, so this
# is a coarse heuristic from email TLD + owner_location + app-URL TLD.
TARGET_CC = {"us", "ca", "uk", "gb", "ie", "fr", "de", "es", "it", "nl", "be", "lu", "at", "ch",
             "se", "no", "dk", "fi", "is", "pt", "pl", "cz", "sk", "hu", "ro", "bg", "hr", "si",
             "ee", "lv", "lt", "gr", "cy", "mt"}
NONTARGET_CC = {"br", "mx", "ar", "cl", "co", "pe", "ve", "uy", "py", "bo", "ec", "gt", "cr", "do",
                "tn", "ma", "dz", "eg", "za", "ng", "ke", "gh", "et", "tz", "ug", "sn", "ci",
                "in", "pk", "bd", "lk", "np", "id", "ph", "vn", "th", "my", "kh", "mm",
                "ae", "sa", "qa", "kw", "om", "jo", "lb", "il", "tr", "ir", "iq",
                "cn", "tw", "hk", "jp", "kr", "ru", "ua", "by", "kz", "au", "nz", "sg"}
NONTARGET_LOC = ("brazil", "brasil", "méxico", "mexico", "argentina", "chile", "colombia", "perú",
                 "peru", "venezuela", "ecuador", "india", "indonesia", "pakistan", "bangladesh",
                 "nigeria", "kenya", "ghana", "egypt", "tunisia", "morocco", "south africa",
                 "philippines", "vietnam", "thailand", "malaysia", "singapore", "dubai", "u.a.e",
                 "uae", "saudi", "turkey", "türkiye", "china", "japan", "korea", "russia", "ukraine",
                 "australia", "new zealand", "sri lanka", "nepal")
TARGET_LOC = ("united states", "usa", " us ", "u.s.", "america", "canada", "united kingdom", "u.k.",
              " uk", "england", "scotland", "wales", "ireland", "london", "germany", "deutschland",
              "france", "spain", "españa", "italy", "italia", "netherlands", "belgium", "sweden",
              "norway", "denmark", "finland", "poland", "portugal", "switzerland", "austria")

def _cc(host):
    last = (host or "").rstrip(".").split(".")[-1].lower()
    return last if len(last) == 2 else ""

def region_class(row):
    loc = " " + (row.get("owner_location") or "").lower() + " "
    if any(k in loc for k in NONTARGET_LOC): return "nontarget"
    if any(k in loc for k in TARGET_LOC): return "target"
    dom = norm(row.get("email")).split("@")[-1]
    url = re.sub(r"^https?://(?:www\.)?", "", (row.get("live_url") or "").lower()).split("/")[0]
    for cc in (_cc(dom), _cc(url)):
        if cc in NONTARGET_CC: return "nontarget"
        if cc in TARGET_CC: return "target"
    return "unknown"

# ---------- timezone scheduler (deliver in each recipient's local morning) ----------
# Resolve recipient tz from coarse signals, deliver inside [target_hour-before, +after] local
# (default 08:00-11:00). Stateless: each run re-checks "is it their window now?"; a recipient not
# in-window is simply deferred to a future run (implicitly next day). All gating math is aware-UTC;
# DST is owned by zoneinfo (IANA names, never fixed offsets). See method/AUTOSEND.md.
CC_TZ = {"us": "America/Chicago", "ca": "America/Toronto", "uk": "Europe/London", "gb": "Europe/London",
         "ie": "Europe/Dublin", "fr": "Europe/Paris", "de": "Europe/Berlin", "es": "Europe/Madrid",
         "it": "Europe/Rome", "nl": "Europe/Amsterdam", "be": "Europe/Brussels", "lu": "Europe/Luxembourg",
         "at": "Europe/Vienna", "ch": "Europe/Zurich", "se": "Europe/Stockholm", "no": "Europe/Oslo",
         "dk": "Europe/Copenhagen", "fi": "Europe/Helsinki", "is": "Atlantic/Reykjavik", "pt": "Europe/Lisbon",
         "pl": "Europe/Warsaw", "cz": "Europe/Prague", "sk": "Europe/Bratislava", "hu": "Europe/Budapest",
         "ro": "Europe/Bucharest", "bg": "Europe/Sofia", "hr": "Europe/Zagreb", "si": "Europe/Ljubljana",
         "ee": "Europe/Tallinn", "lv": "Europe/Riga", "lt": "Europe/Vilnius", "gr": "Europe/Athens",
         "cy": "Asia/Nicosia", "mt": "Europe/Malta"}
LOC_TZ = {"los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
          "california": "America/Los_Angeles", "seattle": "America/Los_Angeles", "portland": "America/Los_Angeles",
          "new york": "America/New_York", "brooklyn": "America/New_York", "boston": "America/New_York",
          "miami": "America/New_York", "atlanta": "America/New_York", "chicago": "America/Chicago",
          "austin": "America/Chicago", "texas": "America/Chicago", "denver": "America/Denver",
          "colorado": "America/Denver", "phoenix": "America/Phoenix", "arizona": "America/Phoenix",
          "london": "Europe/London", "england": "Europe/London", "manchester": "Europe/London",
          "berlin": "Europe/Berlin", "munich": "Europe/Berlin", "paris": "Europe/Paris",
          "stockholm": "Europe/Stockholm", "sweden": "Europe/Stockholm", "amsterdam": "Europe/Amsterdam",
          "madrid": "Europe/Madrid", "dublin": "Europe/Dublin"}
# US state abbrevs — dropped the ones that collide with common words (" or "=conjunction,
# " co "="& Co", " ma "=word) since they'd mis-resolve non-US strings. Checked AFTER explicit
# country/city names (see recipient_tz order) so an abbrev never overrides a real country.
STATE_ABBR_TZ = {" va ": "America/New_York", " ny ": "America/New_York", " fl ": "America/New_York",
                 " ga ": "America/New_York", " nc ": "America/New_York", " tx ": "America/Chicago",
                 " il ": "America/Chicago", " mn ": "America/Chicago", " az ": "America/Phoenix",
                 " ca ": "America/Los_Angeles", " wa ": "America/Los_Angeles"}
LOC_CC = {"united states": "us", "usa": "us", "america": "us", "canada": "ca", "united kingdom": "uk",
          "england": "uk", "scotland": "uk", "wales": "uk", "ireland": "ie", "germany": "de",
          "deutschland": "de", "france": "fr", "spain": "es", "españa": "es", "italy": "it", "italia": "it",
          "netherlands": "nl", "belgium": "be", "sweden": "se", "norway": "no", "denmark": "dk",
          "finland": "fi", "poland": "pl", "portugal": "pt", "switzerland": "ch", "austria": "at"}

def _zone(name, default_name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):   # TypeError: non-string cfg value
        try:
            return ZoneInfo(default_name)
        except Exception:
            return ZoneInfo("America/New_York")

def recipient_tz(row, cfg):
    """Best-effort IANA zone. Explicit city, then explicit COUNTRY, then US-state abbrev, then TLD,
    then default. Country/city before the 2-letter abbrev so 'Germany or remote' resolves to Berlin,
    not Oregon (the abbrev map would otherwise catch the word 'or' first)."""
    default = cfg.get("default_timezone", "America/New_York")
    loc = " " + (row.get("owner_location") or "").lower().strip() + " "
    for kw, tz in LOC_TZ.items():                          # explicit city / region (most specific)
        if kw in loc: return _zone(tz, default)
    for kw, cc in LOC_CC.items():                          # explicit country name beats an abbrev collision
        if kw in loc and cc in CC_TZ: return _zone(CC_TZ[cc], default)
    for tok, tz in STATE_ABBR_TZ.items():                  # US-state abbrev fallback ("Wytheville VA")
        if tok in loc: return _zone(tz, default)
    dom = norm(row.get("email")).split("@")[-1]
    url = re.sub(r"^https?://(?:www\.)?", "", (row.get("live_url") or "").lower()).split("/")[0]
    for host_cc in (_cc(dom), _cc(url)):
        if host_cc in CC_TZ: return _zone(CC_TZ[host_cc], default)
    return _zone(default, default)

def _slot_offset_minutes(email, window_minutes):
    """Deterministic per-recipient offset into the window (spreads sends, stateless)."""
    h = hashlib.sha256(norm(email).encode()).digest()
    return int.from_bytes(h[:4], "big") % window_minutes

def due_now(row, cfg, now_utc):
    """True iff recipient's LOCAL time is in [start,end) AND past their hash offset into the window.
    Once past the offset a recipient stays due until window end, so the due-span is
    (window - offset). We cap the offset at (window - cadence) so EVERY recipient has >= one
    cadence-interval of due-span — otherwise a high-offset recipient (due only in the last minute)
    would be skipped by the coarse fire cadence every single day and never send."""
    local = now_utc.astimezone(recipient_tz(row, cfg))
    ch, bfr, aft = int(cfg.get("target_hour", 9)), int(cfg.get("window_before_hours", 1)), int(cfg.get("window_after_hours", 2))
    start_h, end_h = ch - bfr, ch + aft
    if not (dtime(start_h, 0) <= local.time() < dtime(end_h, 0)):
        return False
    window_minutes = (bfr + aft) * 60
    spread = max(1, window_minutes - int(cfg.get("runner_cadence_minutes", 30)))
    minutes_into = (local.hour - start_h) * 60 + local.minute
    return minutes_into >= _slot_offset_minutes(row.get("email"), spread)

def select(rows, cfg, suppressed, already, cap, now_utc):
    import validate_emails
    out = []
    picked = set()   # emails already selected THIS run — one founder with N repos = one send, not N
    for r in rows:
        if (r.get("status") or "").strip():
            continue
        email = norm(r.get("email"))
        if not email or email in suppressed or email in already:
            continue
        if email in picked:   # same email on another worklist row: dedup within the run (else double-send)
            continue
        if cfg.get("skip_role_addresses") and is_role(email):
            continue
        if cfg.get("region_gate") and region_class(r) == "nontarget":   # drop non-target; keep unknown
            continue
        if cfg["segments"] and r.get("segment") not in cfg["segments"]:
            continue
        # Pre-send mailbox verification (method/VERIFY-INTEGRATION.md). The chain runs verify_queue
        # before this, but only up to verify_max_per_run rows and only if it did not error, so
        # "nothing is mailed unverified" is a claim about ORDERING, not a guarantee — the worklist
        # sort happens to put the sender's first rows inside the verify limit today, and a change to
        # either sort silently breaks it. This flag makes it structural: an unverified row is simply
        # not sendable. Defaults off so enabling verification is still a deliberate two-step.
        if cfg.get("require_verified") and not (r.get("verify_status") or "").strip():
            continue
        if cfg.get("require_email_valid"):
            ev = (r.get("email_valid") or "").strip().lower()
            if ev == "":
                ok, _ = validate_emails.check(email); ev = "true" if ok else "false"
            if ev != "true":
                continue
        if cfg.get("tz_scheduler") and now_utc is not None and not due_now(r, cfg, now_utc):   # not their local morning yet -> defer
            continue
        out.append(r)
        picked.add(email)
        if len(out) >= cap:
            break
    return out

def live_ok(cfg, subject_t, body_t):
    """All three gates must pass to send live. Returns (ok, reason)."""
    if cfg.get("dry_run", True) is not False:
        return False, "dry_run is not literally false"
    if cfg.get("template_approved", False) is not True:
        return False, "template_approved is not true (flip it deliberately when copy is final)"
    if "[PLACEHOLDER" in (subject_t + body_t):
        return False, "template still contains the [PLACEHOLDER] block"
    if not subject_t.strip() or len(body_t.strip()) < MIN_BODY:
        return False, f"subject empty or body under {MIN_BODY} chars (looks unfinished)"
    return True, ""

# ---------- run ----------
def run(config_path, limit, ignore_window=False):
    cfg = load_json(config_path)
    now = datetime.now()                               # machine-local: run_id, today, ts, cap-bucket
    now_utc = datetime.now(timezone.utc)               # aware-UTC: ALL window/tz math
    run_id = now.strftime("%Y%m%dT%H%M%S")
    dry = cfg.get("dry_run", True) is not False        # live ONLY if literally false
    os.makedirs(SEND, exist_ok=True)
    if cfg.get("tz_scheduler"):                        # fail fast on a default_timezone typo
        _zone(cfg.get("default_timezone", "America/New_York"), "America/New_York")

    if os.path.exists(STOP):
        print("[send] STOP file present — halting, no send."); return
    if os.path.exists(HALT):
        print(f"[send] HALT present ({open(HALT).read().strip()}) — clear it to resume."); return

    subject_t, body_t = load_template(cfg["template"])
    ok, why = live_ok(cfg, subject_t, body_t)

    if not dry:
        # exclusive lock so an overlapping launchd cycle can't double-send
        lock_fh = open(LOCK, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[send] another live send holds the lock — exiting (no double-send)."); return
        if not ok:
            print(f"[send] REFUSING live send: {why}."); return

    log_rows = read_csv(SEND_LOG)
    live_log = [r for r in log_rows if r.get("mode") == "live"]
    # write-ahead: 'pending' means the Gmail call may have happened; treat as sent, warn to reconcile
    pending = [r for r in log_rows if r.get("mode") == "pending"]
    live_to = {norm(r.get("to")) for r in live_log}
    orphan_pending = {norm(r.get("to")) for r in pending} - live_to
    if orphan_pending:
        print(f"[send] WARNING: {len(orphan_pending)} pending send(s) never confirmed live "
              f"(prior crash?). Treating as sent (won't resend). Check: {sorted(orphan_pending)[:5]}")
    already = live_to | {norm(r.get("to")) for r in pending}

    reason = breaker_reason(cfg, live_log)
    if reason and not dry:
        with open(HALT, "w") as f: f.write(f"{run_id} circuit breaker: {reason}")
        print(f"[send] CIRCUIT BREAKER TRIPPED — {reason}. Wrote HALT, no send."); return
    if reason:
        print(f"[send] (dry-run) breaker WOULD trip: {reason}")
    if cfg.get("breaker_enabled", True) is False:
        nb = len({norm(b.get("email")) for b in read_csv(BOUNCES) if (b.get("type") or "hard") == "hard"})
        print(f"[send] BOUNCE BREAKER DISABLED — no automatic halt. {nb} hard bounce(s) on record. "
              f"No halt on bounce RATE; the adaptive throttle scales volume instead. HALT, the daily "
              f"cap and STOP still stop this run.")

    if not dry and not cfg.get("tz_scheduler") and not in_window(cfg, now):   # tz_scheduler replaces the global window
        print(f"[send] outside send window {cfg['send_windows']} (now {now:%H:%M}) — skipping."); return

    today = now.strftime("%Y-%m-%d")
    sent_today = sum(1 for r in live_log if (r.get("ts") or "").startswith(today))
    mult, tnote = throttle_factor(cfg, live_log)
    effective_cap = int(round(int(cfg["daily_cap"]) * mult))
    print(f"[send] throttle: {tnote} (cap {cfg['daily_cap']} -> {effective_cap})")
    if mult == 0.0 and not dry:
        # The stop tier writes HALT on purpose. Returning with only a print would be a PERMANENT
        # stop with no on-disk marker — worse than the breaker it replaces, because the trailing
        # window is frozen the same way (no send can occur, so no clean send can dilute it) but
        # nothing in the chain log says why. HALT is echoed every cycle by run_chain.sh and needs a
        # human to clear, which is the right call at a >=20% bounce rate.
        _write_halt(run_id, f"adaptive throttle stop tier: {tnote}")
        print(f"[send] THROTTLE STOP — {tnote}. Wrote HALT, no send. Verify the list "
              f"(pipeline/verify_queue.py), then clear HALT to resume."); return
    cap = max(0, effective_cap - sent_today)
    if limit is not None:
        cap = min(cap, limit)
    if cap <= 0 and not dry:
        print(f"[send] daily cap {cfg['daily_cap']} reached ({sent_today} today) — done."); return
    if dry:
        cap = limit if limit is not None else 10 ** 9

    rows = read_csv(os.path.join(ROOT, cfg["worklist"]))
    # ignore_window (dry-run review only) passes now_utc=None -> select skips the tz-due gate so you
    # can see every render regardless of the current hour.
    selected = select(rows, cfg, load_suppression(), already, cap, None if (dry and ignore_window) else now_utc)
    if not selected:
        due = " (none in their local send window right now)" if cfg.get("tz_scheduler") and not ignore_window else ""
        print(f"[send] nothing sendable{due}."); return

    if dry:
        for f in (os.listdir(DRYRUN) if os.path.isdir(DRYRUN) else []):
            os.remove(os.path.join(DRYRUN, f))
        os.makedirs(DRYRUN, exist_ok=True)
        from collections import Counter
        tzc = Counter()
        for i, r in enumerate(selected):
            tz = str(recipient_tz(r, cfg)) if cfg.get("tz_scheduler") else ""
            tzc[tz] += 1
            with open(os.path.join(DRYRUN, f"{i:03d}_{r['owner_repo'].replace('/','_')}.txt"), "w") as fh:
                fh.write(f"To: {r['email']}\nFrom: {cfg['from_name']} <{cfg['from_address']}>\n"
                         f"Segment: {r.get('segment')}  Trend: {r.get('trend')}  TZ: {tz}\n"
                         f"Subject: {render(subject_t, r)}\n\n{render(body_t, r)}\n")
        scope = "all selectable (window ignored)" if ignore_window else "in-window now"
        print(f"[send] DRY-RUN: {len(selected)} rendered ({scope}) to {os.path.relpath(DRYRUN, ROOT)}/ — nothing sent.")
        print(f"[send] live gate: {'READY' if ok else 'BLOCKED — ' + why}")
        if cfg.get("tz_scheduler"):
            print(f"[send] recipient timezones: {dict(tzc.most_common())}")
        print(f"[send] would send from {cfg['from_address']} · cap {cfg['daily_cap']}/day · segments {cfg['segments']}")
        return

    # ---- LIVE ----
    import gmail_auth
    try:
        svc = gmail_auth.service()
    except Exception as e:
        if _auth_fatal(e):   # dead token at client build (the silent-outage case) -> HALT loudly, don't crash
            _write_halt(run_id, f"building Gmail client: {e}")
            print(f"[send] AUTH FAILURE building client: {e} — wrote HALT, no send (re-consent, then clear HALT)."); return
        raise
    frm = f"{cfg['from_name']} <{cfg['from_address']}>"
    worklist_path = os.path.join(ROOT, cfg["worklist"])
    sent = 0
    for r in selected:
        if os.path.exists(STOP):
            print("[send] STOP appeared mid-run — stopping."); break
        if os.path.exists(HALT):
            print("[send] HALT appeared mid-run — stopping."); break
        now = datetime.now()
        if cfg.get("tz_scheduler"):
            if not due_now(r, cfg, datetime.now(timezone.utc)):   # window closed during a long run -> skip THIS one, others may still be due
                continue
        elif not in_window(cfg, now):
            print(f"[send] left send window ({now:%H:%M}) — stopping."); break
        # Re-check against the THROTTLED cap, not the raw daily_cap. A full-cap pass spans hours at
        # 90-240s jitter; using cfg["daily_cap"] here let a throttled run keep sending up to the
        # unthrottled ceiling, so the throttle was advisory-only after selection.
        if sent_today + sent >= effective_cap:
            print(f"[send] cap {effective_cap} reached mid-run — stopping."); break
        to = norm(r.get("email"))
        base = {"ts": datetime.now().isoformat(timespec="seconds"), "to": to,
                "owner_repo": r.get("owner_repo"), "segment": r.get("segment"),
                "subject": render(subject_t, r), "run_id": run_id}
        append_log({**base, "message_id": "", "mode": "pending"})   # write-ahead BEFORE send
        try:
            mid = gmail_auth.send(svc, to, base["subject"], render(body_t, r), from_addr=frm)
        except Exception as e:
            if _auth_fatal(e):   # dead/revoked token: provably not delivered AND global — roll back this
                _rollback_pending(to, run_id)   # lead's pending (don't park as sent) and abort the batch
                _write_halt(run_id, str(e))
                print(f"[send] AUTH FAILURE ({e}) — rolled back pending for {to}, wrote HALT, aborting run "
                      f"(re-consent the token, then clear HALT)."); break
            append_log({**base, "message_id": "", "mode": "error"})   # transient/ambiguous: keep pending+error
            print(f"[send] ERROR to {to}: {e} — logged, continuing."); continue
        append_log({**base, "message_id": mid, "mode": "live"})
        _mark_sent(worklist_path, r.get("owner_repo"), to, today)
        sent += 1
        print(f"[send] sent {sent}/{len(selected)} -> {to} ({r.get('owner_repo')})")
        if sent < len(selected):
            time.sleep(random.uniform(*cfg["jitter_seconds"]))
    print(f"[send] done — {sent} sent this run, {sent_today + sent}/{effective_cap} today"
          + (f" (throttled from {cfg['daily_cap']})" if effective_cap != int(cfg["daily_cap"]) else "") + ".")

def _mark_sent(worklist_path, owner_repo, email, today):
    rows = read_csv(worklist_path)
    if not rows: return
    cols = list(rows[0].keys())
    for r in rows:
        if r.get("owner_repo") == owner_repo and norm(r.get("email")) == email and not (r.get("status") or "").strip():
            r["status"] = "sent"; r["contacted_on"] = today; r["channel"] = "email"
    tmp = worklist_path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    os.replace(tmp, worklist_path)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(SEND, "config.json"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ignore-window", action="store_true",
                    help="dry-run only: render every selectable row regardless of tz window (copy review)")
    a = ap.parse_args()
    run(a.config, a.limit, ignore_window=a.ignore_window)

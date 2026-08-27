# SPEC — the platform-Graduate Identification, Enrichment & Monitoring Pipeline

_Prepared for: Claude Code, to review and turn into an implementation plan. Date: 2026-06-24._

> **Claude Code: do not build yet.** Read the companion files in section 1, review this spec, challenge anything wrong or under-specified, then produce an implementation plan (architecture, file layout, task breakdown, risks). Build after the operator approves the plan.

---

## 0. Why this exists (the short version)

We want to find, contact, and keep finding a specific person: a **prosumer who built a real product on the platform (lovable.dev) and has moved on to a pro coding tool (Claude Code, Codex, or Cursor) on their own** because the platform got too expensive, too constraining, or trapped them in a fix-one-break-ten loop. This cohort is unusually detectable because the platform leaves durable marks on both the deployed app and the GitHub repo, and graduates narrate the move in public.

This was scoped and partly proven inside Cowork; Cowork's fetch sandbox can't do the data work at scale (it blanks GitHub JSON, is rate-limited on a shared IP, and forbids curl). Claude Code can. This spec hands the whole job over.

---

## 1. Read these first (context lives in this repo)

All paths relative to the Projects root (`<projects-root>/`).

- the internal research notes — **the canonical signal taxonomy.** The four-layer model, the verified fingerprints, the honest caveats. This spec compresses it; that file is the source of truth.
- the constrained first pass (the 2 verified graduates, the constraints, the language read).
- a stdlib-only starter script (population → marker filter → commit-email). Treat as a reference sketch to improve, not the design.
- the internal research notes — the dev-platform thesis this cohort feeds (the co-founder's "builder who can code but doesn't want to live in the terminal").
- the internal research notes — Profile B, the person-fit model. Note: it scopes prosumers OUT of outbound pending a configuration choice (see open questions).
- `intelligence-map.md` — the research index; register any new file under stream 8l.

---

## 2. Definitions (target vs adjacents)

| Type | Tell | For us |
|---|---|---|
| **Graduate self-solver** (TARGET) | "It kept breaking / burning credits, so I exported to GitHub and moved to Claude Code / Cursor / Codex." Owns the code, names a next tool, wires its own backend. | The lead. |
| Stuck quitter | "…so I gave up." No next tool, nothing shipped, betrayal framing. | Exclude. |
| Stuck-and-pay (rescue customer) | "…so I hired someone." Reaches for a human. | Adjacent; proves the market, not the self-serve buyer. |
| Happy stayer | "the platform's ceiling rose, plan mode fixed it." | Not in-market. |

**Discriminator:** a complaint plus a *leveling-up token* (exported, GitHub, Cursor, Claude Code, Codex, Supabase, VS Code) = graduate. A complaint plus "gave up" or "hire someone" = not.

**Destinations (2026):** Claude Code dominant, Cursor second, Codex rising. Bolt / v0 / Replit are peer starting points, **not** graduation destinations.

---

## 3. Scope

**In:** GitHub-discoverable graduates; their live app; their contact (email first, socials second); a scored list; a recurring monitor; a Reddit/discourse listener.

**Out (for now):** outreach automation/sending; CRM writeback; non-GitHub-discoverable graduates (people who never pushed to a public repo); private repos.

**Fit gate (carry into scoring, do not hard-drop):** The product runs on client-rendered SPA frontends (React/Angular/Vue) with an accessible repo. the platform's default stack is React/Vite, so most graduates pass by construction.

---

## 4. Signal taxonomy (compressed — full version in the memo)

- **Layer A — repo/app fingerprints (scalable, the spine).** the platform origin marker + a pro-tool marker = graduate.
- **Layer B — discourse/behavioral.** Public migration posts; the self-solver markers; live constraint complaints (timing).
- **Layer C — enumerable lists.** madewithlovable.com, lovable.dev/experts, platform showcase winners, Product Hunt.
- **Layer D — market proxy.** Rescue firms + the "fixing vibe-coded businesses" discourse (validates the cohort; not per-person).

The build is mostly Layer A + C for identification, Layer B for timing/enrichment, Layer D as background.

---

## 5. Pipeline requirements

Each stage states the REQUIREMENT, a RECOMMENDED method (Claude Code may improve it in planning), and the GOTCHAS we already know.

### Stage A — Population
- **Requirement:** enumerate platform-origin repos, freshest first, forks excluded.
- **Recommended:** authenticated GitHub. Repos search `"lovable.dev/projects" in:readme sort:updated` (≈284,919 repos as of 2026-06-24, count drifts, default excludes forks). Paginate. Add `pushed:>2026-03-01 stars:>0` to bias toward active, non-throwaway repos. Secondary markers: `"Welcome to your the platform project" in:readme`, `"the platform Generated Project" in:readme` (~13k), `topic:lovable`.
- **Gotchas:** the marker phrase is the highest-recall repo tell; tutorial/clone/template repos pollute it (filter by name/description containing tutorial, clone, example, template, boilerplate, migration, starter, lovable-to-, and require a real `lovable.dev/projects/{uuid}` URL).

### Stage B — Graduate filter
- **Requirement:** keep only repos that ALSO show a pro-tool marker; flag "lovable-tagger removed" as a stronger graduate signal.
- **Recommended, two options for CC to weigh in planning:**
  1. **Per-repo contents check** (simple): for each population repo, read the root tree, look for `CLAUDE.md` / `.claude/` (Claude Code), `.cursorrules` / `.cursor/` (Cursor), `AGENTS.md` / `.codex/` (Codex). Cheap with auth (5,000 core/hr) or via GraphQL batching.
  2. **Reverse code-search** (efficient at scale): authenticated code search for the marker files that reference the platform, e.g. files named `.cursorrules` / `CLAUDE.md` / `AGENTS.md` containing "lovable". This can surface graduates directly without scanning the full population. Code search needs auth (which CC has).
- **Stronger signal:** repo carries the the platform README/history AND `lovable-tagger` is absent from `vite.config.ts`/`package.json` (a clean migration strips it).
- **Destination inference:** which marker file present → Claude Code / Cursor / Codex.

### Stage C — Enrichment (the part Cowork couldn't do)
- **Requirement:** for each graduate, resolve live app, owner identity, and a contact — email first.
- **Email (primary):** read the git commit author email. Two reliable methods:
  - `gh api repos/{owner}/{repo}/commits` (JSON, `commit.author.email`), or
  - shallow clone + `git log --format='%ae|%an' | sort -u` (gets every author incl. co-authors).
  - Filter out `*noreply*` and `*[bot]*` (the platform's bot, GitHub privacy addresses). **Expect a usable real email on ~50%**; privacy-on users yield only noreply.
- **Live app:** the repo `homepage` field; else try `{repo}.lovable.app` or a custom domain found in README/meta. Firecrawl the live app to confirm it resolves and to scrape an on-site contact / linked socials.
- **Socials:** GitHub profile (`blog`, `twitter_username`, `company`); Firecrawl the personal site / X bio for email + LinkedIn. **Apollo is a weak fallback here** — these are solo builders with no company, so Apollo usually returns empty; only use it when a real employer is present.

### Stage D — Scoring
- **Requirement:** a 0–5 self-solver score per graduate.
- **Recommended rubric:** +1 live app resolves; +1 graduate marker present; +1 pushed in last 90 days; +1 a usable contact found; +1 looks like a real product (name/description/app, not a toy/demo). Capture destination and any "No the platform Credits Required" / migration language from the README or marker file as evidence.

### Stage E — Discourse listener (Layer B)
- **Requirement:** surface live migration/exit threads and capture verbatim language (for timing + messaging + the preference question in section 9).
- **Recommended:** Reddit API (CC can register a read-only app) or Firecrawl over Reddit. Subs: r/lovable (~49–50k), r/vibecoding (~281k), r/ChatGPTCoding, r/codex, r/cursor, r/ClaudeAI. Queries: migrate / leaving / "moved to" / export / "credit burn" + claude code|cursor|codex. Also the RSS feeds `https://www.reddit.com/r/lovable/search.rss?q=migrate&restrict_sr=1&sort=new` (and leaving/export/cursor variants).
- **Specifically capture preference language** (settles the section-9 open question): "I miss the visual editor / preview," "wish there were a visual way," back-to-the platform returns.

### Stage F — Output + dedupe
- **Requirement:** a durable, append-only store keyed by repo + owner, so re-runs are incremental and never double-count.
- **Recommended:** a CSV (human) plus a JSON/SQLite store (dedupe key = `owner/repo`). Schema in section 8.

### Stage G — Monitor
- **Requirement:** scheduled incremental re-run (weekly) that adds only new graduates and new discourse threads, and writes a dated digest.
- **Recommended:** cron / launchd / a CI schedule. Reuse stages A–F with a "since last run" filter (`pushed:>{last_run}`). Output a `tracker.md` dated section + new rows in the store. (A Cowork scheduled stub already exists: `this campaign`; the real engine should live in Code.)

---

## 6. Verified technical reference (don't re-derive)

**Repo fingerprints (verified live 2026-06-24 unless noted):**
- Population: `"lovable.dev/projects" in:readme` ≈ 284,919 repos (drifts; excludes forks).
- README boilerplate: heading "Welcome to your the platform project"; line "**URL**: https://lovable.dev/projects/{uuid}"; stack list Vite/TS/React/shadcn/Tailwind.
- Dependency tell: `lovable-tagger` in `vite.config.ts` (`import { componentTagger } from "lovable-tagger"`) / `package.json` devDeps. Its REMOVAL = migration signal.
- Commit author for the platform-synced commits: `lovable-dev[bot]`.

**Live-app fingerprints:**
- Current: `id="lovable-badge"`, `aria-label="Edit with the platform"`, badge href `lovable.dev/projects/{uuid}?utm_source=lovable-badge`.
- Badge-removed but durable: `/lovable-uploads/` asset paths, `lovable-preview-mode` cookie, `LOV_SELECTOR_SCRIPT_VERSION` JS global, `*.lovable.app` script src.
- At scale: PublicWWW `"cdn.gpteng.co"` ≈55,612, `"lovable-badge"` ≈40,125; BuiltWith `trends.builtwith.com/framework/the platform` ≈208,842 (vendor-reported, likely inflated).
- **Retired (do not use):** the old `cdn.gpteng.co/gptengineer.js` script tag — gone on 2026 apps.

**Graduate markers:** `CLAUDE.md` / `.claude/` (Claude Code) · `.cursorrules` / `.cursor/` (Cursor) · `AGENTS.md` / `.codex/` (Codex).

**Meta-list:** repos named for the migration are themselves a lead surface; e.g. `<a popular migration-guide repo>` — its stargazers/forkers are graduates.

**Enumerable lists:** madewithlovable.com (476+ projects with maker X/Reddit handles); lovable.dev/experts (agencies + hire counts).

**Test fixtures (the build must reproduce these as graduates):**
- `ownerfour/wellness-app-1234abcd` → `.cursorrules` (Cursor); README section saying the app deploys without platform credits; Stripe + Supabase auth/RLS + i18n; live at wellness-app-1234abcd.vercel.app.
- `ownerfive/remix-of-bracket-app` → `AGENTS.md` (Codex); React/Vite/Supabase/Vercel; live at bracketstats.example.com; README retains the platform origin with the project ID reset (auto-sync detached).

---

## 7. Tooling notes for Claude Code

- **Auth:** `gh auth login` or a fine-grained read-only PAT (Public Repositories, read-only) in `GITHUB_TOKEN`. Auth lifts core to 5,000/hr and unlocks code search.
- **GitHub:** prefer `gh api` / `gh search` and the GraphQL API (batch repo + tree + commits in one call to cut request count). Raw curl works too.
- **Email:** `git log` on shallow clones is the most complete; `gh api .../commits` is lighter.
- **Firecrawl:** use for JS-rendered pages the GitHub API can't give — Reddit threads, madewithlovable.com, live-app pages, X/personal-site bios for contact resolution.
- **jq / ripgrep:** parsing and marker scanning.
- **Apollo:** fallback enrichment only when a real employer is attached (rare for this cohort).
- **Politeness:** respect GitHub secondary limits and Reddit/Firecrawl rate limits; cache aggressively; the monitor should be incremental, not a full re-scan.

---

## 8. Output schema (minimum fields)

`owner/repo` (dedupe key) · owner_login · github_profile_url · live_app_url · markers · destination · lovable_tagger_present (bool) · stars · last_push · emails (real, noreply dropped) · x_handle · personal_site · self_solver_score · evidence_snippet (README/marker migration language) · first_seen · last_seen · source (repo-search | code-search | madewithlovable | meta-list).

Two artifacts: `graduates.csv` (human) + a dedupe store (`graduates.json` or SQLite).

---

## 9. Pitfalls & lessons (from the Cowork attempt — save CC the rediscovery)

- **The Cowork blockers do NOT apply to Code:** Cowork's fetch tool blanked GitHub JSON/atom and forbade curl; that's why this moved here. Code has no such limit.
- **GitHub search counts drift** between identical queries (saw 284,919 vs 308,471). Don't treat the count as exact.
- **~50% of commit emails are GitHub noreply / the platform-bot** addresses — unusable. Plan for partial email yield; keep socials + app domain as the fallback contact path.
- **Tutorial/clone/template repos pollute** the population marker; filter hard and require a real `{uuid}`.
- **Attribution discipline:** an evidence snippet must be the builder's own words from their repo/post. Do not infer intent.
- **The terminal-preference trap (important, corrected after review):** that a graduate uses Cursor/Codex does NOT prove they prefer the terminal — those are the only graduation paths on offer, so tool choice shows availability, not preference. Capture *language* about what they miss (visual editor/preview) before drawing any preference conclusion. Score on observable facts, not inferred preference.
- **ToS:** Reddit and Discord scraping have ToS limits; prefer official APIs / Firecrawl within terms. Discord member-reading requires joining; do not auto-DM.

---

## 10. Acceptance criteria

1. Reproduces both section-6 test fixtures as graduates, with correct destination and a recovered email OR a documented noreply.
2. Produces ≥40 scored graduates from a single authenticated run, with live-app URLs and a usable email on a meaningful share (target ≥40%).
3. Dedupe store makes a second run add only new rows.
4. Discourse listener returns ≥10 recent (last 30 days) migration threads with verbatim snippets and named trigger + destination.
5. Monitor runs unattended on a weekly schedule and writes a dated `tracker.md` digest.
6. Every load-bearing number in the output is reproducible from a logged query; no inferred fields presented as observed.

---

## 11. Ethics & compliance

Public data only. This is B2B founder-led outreach by the operator from a personal address; respect CAN-SPAM / GDPR-style norms (clear identity, easy opt-out, no deception). Honor robots/ToS for Reddit, Discord, and scraped sites. Rate-limit politely. Do not store sensitive personal data beyond name / public email / public handles / public repo facts.

---

## 12. Open questions for the operator / the co-founder (resolve before scaling outreach)

1. **Prosumer scope.** Profile B currently scopes prosumers out of outbound. This pipeline targets prosumers. Confirm they are in scope, or that this is a dev-platform distribution experiment only.
2. **Which graduate is the buyer** — the terminal-comfortable power user, or the one who wants a graphical, non-terminal path on owned code (the dev-platform bet)? The discourse listener should inform this; don't hard-target until decided.
3. **Price / offer.** Most graduates are content at $20 flat on Claude Code. What does your product offer them that beats "I already have my agent"? (Product question, gates outreach value.)
4. **Contact threshold.** Minimum acceptable email yield before this is worth a send motion vs. a warm/community motion.

---

## 13. References

- Repo files in section 1.
- GitHub REST + GraphQL + code-search docs; `gh` CLI.
- Firecrawl docs.
- External fingerback sources (in the canonical memo's Sources section): registry.npmjs.org/lovable-tagger, GitHub repositories API, PublicWWW, BuiltWith, docs.lovable.dev, 404 Media (rescue market).

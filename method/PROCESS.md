# the platform-graduate identification — current process

_Last updated 2026-07-06. Canonical reference for how accounts are identified. Three parallel streams: the REPO stream (the "accounts"), the DISCOURSE stream (warm hand-raisers + messaging language), and the CEILING stream (pre-graduation builders — see `method/CEILING.md`)._

## Locked decisions (a configuration choice)
- **Validate first** — produce lists and talk to people; no standing monitor yet.
- **Warm-first** outreach; harvested email is secondary (clear identity, opt-out, skip EU/private-relay).
- **This campaign's outbound, prosumers in scope** (overrides Profile B exclusion for this cohort).
- **Region = founder location**, not product market.
- **High-confidence region only** is auto-actionable; medium/low get a manual pass.
- ~~**Do not scan marker-less platform-origin repos** (no reliable pre-graduation precursor found).~~ _Superseded 2026-07-06: the ceiling stream scans marker-less repos on BEHAVIORAL signals (commit classes), which the 2026-06-24 precursor test never covered — that test rejected file artifacts only, and its "graduates detach" premise came from a matcher bug (see "Tested and rejected"). Method + evidence: `method/CEILING.md`._
- **Pre-grad ceiling cohort is a separate stream** (2026-07-06) — own worklist + message; segment priority B over A; 60-day floor on B only; rank don't cull; boomerangs stay in the graduate stream.

## Repo identification — gates in order
1. **Population.** GitHub `"lovable.dev/projects" in:readme`, **no star floor**, sliced by `pushed:>{date}` freshest-first. (Search hard-caps at 1,000 results; walk by date window. ~285k total exist; ~4.7k/week.)
2. **Junk drop.** Exclude forks + names/descriptions with tutorial/clone/template/boilerplate/starter/awesome/-list.
3. **Origin verify.** Require a real the platform UUID in README (`lovable.dev/projects/{8-4-...}`). Drops keyword noise.
4. **Graduate breadcrumb.** Require a marker: `CLAUDE.md`/`.claude` (Claude Code), `.cursorrules`/`.cursor` (Cursor), `AGENTS.md`/`.codex` (Codex). Marker-less platform-origin repos are dropped — accepted blind spot (precision over recall). Destination is a set (multi-marker is normal).
5. **Web-only.** Read `package.json`: native (react-native/expo/flutter/nativescript) → DROP; `@capacitor/*`/`@ionic/*` → KEEP + FLAG (web SPA underneath); else web.
6. **Contact resolve.** Commit-author email with hardened denylist (noreply, [bot], bot@, @anthropic.com, @cursor.com, cursoragent, @example.com, .local/.home/.lan, privaterelay.appleid.com, github-actions, @sqi.app, users.noreply) + profile location/X/blog/company + homepage (live app).
7. **Model judgment.** Reads README + marker file + commit subjects → `graduate_self_solver` vs `day_one_tooling` vs `toy_or_demo` vs `quitter`. Requires ownership/migration evidence for graduate (custom marker, self-host, own deploy, "No the platform Credits"); auto-generated boilerplate CLAUDE.md = day_one. Scores self_solver 0-5; Tier A / B / skip.
8. **Live-app verify.** Load the app: verified = HTTP 200 + real title (not Vite default) + not 404/parked. 404 on a recently-pushed repo = moved/private (manual check), not dead.
9. **Region.** Signals: profile location + commit-author timezone (from blobless clone — REST normalizes to UTC) + email/app TLD + content language → model classifies TARGET (USA/Canada/UK/Europe, founder location) vs NON_TARGET vs UNKNOWN + confidence. High-confidence TARGET = auto-actionable.

## Funnel (last-week sample, real)
4,671 pushed-last-week → 398 scanned freshest → 42 origin+marker verified → 40 web-only → judged → 14 Tier-A + 15 Tier-B → region.
Combined judged pool: 94 → 49 graduates (A/B) → 24 target-region → 15 high-confidence target (5 A, 10 B).

## Tested and rejected
- Star floor (>0/>1) — removed; deleted real zero-star products.
- lovable-tagger removal as signal — dropped (48/58 graduates kept it).
- Reverse code-search as primary spine — supplement only (catches markers that mention "lovable").
- madewithlovable.com — excluded (current current users/stayers, not graduates).
- Bot-continuation / pre-graduation precursor (own Supabase/CI/deploy/domain before marker) — no reliable signal in FILE ARTIFACTS; that part stands. _Correction 2026-07-06: the companion bot-continuation analysis (`analysis/timeline_bot_truth.csv`) used a broken matcher (`"lovable" in email` misses the bot's main `gpt-engineer-app[bot]` git identity; only the rare `noreply@lovable.dev` template identity matched); its "26/28 graduates have zero bot commits / graduates detach" conclusion is retracted (corrected data: `analysis/bottruth_corrected_2026-07-06.csv`). Corrected via contributors API: 26/26 reachable graduates HAVE bot commits, 14/26 kept the bot active after the tool marker (hybrids). Bot velocity mostly RAMPS into graduation (12/26 increasing pre-marker) — "burst, not a ramp" applies to file artifacts only. See `method/CEILING.md`._

## Discourse stream (parallel, not "accounts")
Reddit RSS across r/lovable, r/vibecoding, r/ChatGPTCoding (search.rss works from Code; .json is 403). 135 posts → 65 warm hand-raisers + 84 verbatim wedge quotes. Separate self-identified population; feeds messaging + the in-market timing signal, not the repo list.

## Scripts (reproducible)
`build_candidates.py` / `build_lastweek.py` (pull) · `pull_discourse.py` (RSS) · enrich + judge via Workflow · `augment_lastweek.py` (mobile + live verify) · `region_signals.py` + `region_tz.py` + region-classify Workflow + `region_assemble.py` · `precursors.py` (rejected-signal test).

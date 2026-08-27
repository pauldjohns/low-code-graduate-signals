# Ceiling stream — pre-graduation the platform builders

_Added 2026-07-06. Canonical reference for the third stream: marker-less the platform builders showing readiness signals, reached BEFORE they graduate to Claude Code/Cursor/Codex. Separate cohort from the repo-graduate stream: own worklist (`outreach/worklist_ceiling.csv`), own message ("you're pushing the platform's ceiling — keep visual+chat, add power"), own store (`store/ceiling_seen.csv`). Script: `pipeline/ceiling_poll.py`._

## Locked decisions (a configuration choice)
- Pre-grads are a **separate cohort** from graduates — separate worklist and outreach message.
- Segment priority **B over A** ("let's see what the data produces" — the stratified sample tests it).
- **60-day age floor applies to Segment B only** — nothing is age-gated out of A or C.
- Rank attention, cull nothing: score/segment order the list; every gate-passer is stored.
- Boomerangs (marker present, bot resumed) stay in the **graduate** stream as a re-classification question.

## Why this supersedes the old "do not scan marker-less repos" lock
The 2026-06-24 precursor test rejected **file artifacts** (own supabase/CI/deploy/domain/tests/docs) as graduation predictors — that rejection stands (best artifact: docs 36% before marker; ≥2 artifacts: 7/49). But two things changed:
1. **Behavioral signals were never tested.** the platform's in-editor edits are committed by a single identifiable identity (`gpt-engineer-app[bot]` / login `lovable-dev[bot]`), so commit streams classify cleanly into platform-bot / infra-bot / web-flow / local-git-human. That's a different signal family from file artifacts.
2. **The bot-truth analysis had a matcher bug.** `archive/scripts/clone_bot.py` matched `"lovable" in email`, which misses the bot's main git identity (`gpt-engineer-app[bot]@users.noreply.github.com`) — it only caught the rarer `noreply@lovable.dev` template identity, which is why the old analysis found 2/28 rather than 0/28. Corrected 2026-07-06 via the contributors API (`analysis/bottruth_corrected_2026-07-06.csv`): **26/26 reachable graduates have the platform-bot commits**, and **14/26 are hybrids — the bot kept committing AFTER the pro tool arrived**. The "graduates detach" premise was false; the synced repo is where the pre-grad story lives. ("Graduation is a burst, not a ramp" holds for file artifacts only; bot velocity, below, mostly RAMPS into graduation.)

## Retrodiction (would this cohort have caught the known graduates pre-marker?)
Scored each dated graduate as of the day before its marker landed (n=26, `analysis/retrodiction_2026-07-06.csv`): **22/26 catchable** — 12 by Segment A, 10 by Segment B. Pre-marker bot-velocity trend: 12 increasing / 4 steady / 5 dropping / 5 sparse+new — builders ramp the platform spend into graduation. 11/26 already showed AI-agent commit trailers before any marker file (the shadow screen catches tool adoption earlier than the marker gate).

## Commit classes (the signal core)
- **platform_bot** — author login `lovable-dev[bot]`/`gpt-engineer-app[bot]`, email contains `gpt-engineer-app[bot]` or `noreply@lovable.dev`. One bot commit ≈ one prompt ≈ credit spend.
- **infra_bot** — `author.type == "Bot"`, actions/dependabot/renovate/`actions-user` (type "User"!), `action@github.com`, `[bot]` names/emails. Never counts as human.
- **web_flow** — committer `GitHub <noreply@github.com>` (web-UI edits, push bridges). Weak signal.
- **local_git** — committer == author, real email. The only class that means "opened a terminal."
- **agent evidence** — `Co-Authored-By: Claude/Cursor/Codex`, `Generated with`, cursoragent/devin/aider identities → shadow graduate.
GitHub's `commits?author=` filter does NOT resolve bot logins or emails — count identities via `contributors?anon=1` (one call, whole history) and classify pages client-side. `stats/commit_activity` is unreliable (202 async); use Link-header `rel="last"` counts with `since`/`until` windows instead.

## Gates in order (all deterministic; see `pipeline/ceiling_poll.py`)
1. **Population.** Same spine as the graduate stream: `"lovable.dev/projects" in:readme fork:false`, walked in single pushed-day slices (two-day windows already exceed the 1k search cap), split into 6h ranges past ~950.
2. **Junk drop.** Same SKIP list as `daily_poll.py`.
3. **Origin verify.** Real the platform UUID in README (raw fetch; also harvests live-app URLs from the README).
4. **Web-only.** Same native drop as the graduate stream.
5. **Marker split** (root contents): marker present → graduate stream (and `graduated_on` stamp if the repo is in the ceiling store — the conversion metric). Windsurf/Cline/Gemini markers → `already_tooled_other`, routed out. Marker-negative → this stream.
6. **Cross-refs.** Skip repos already in `store/seen_store.csv` and owners already in `outreach/worklist_repo.csv` (owner-level double-contact guard). Ceiling store keyed by numeric `repo_id` (rename-safe).
7. **Scoring** (net-new passers): contributors + newest-100 commits page → classes, 30-day counts, revert rate, agent screen.
8. **Segments.**
   - `A_hybrid` — platform_bot ≤30d AND ≥3 local_git in 30d. Duct-taping the platform + outside editor.
   - `B_all_bot` — ≥95% bot share, bot ≤7d, **true age ≥60d** (first-commit date via Link-trick, NOT `created_at` — the platform imports history on late connect; unknown age fails CLOSED to `B_age_unknown`), seriousness (signal deps / own backend / tests / ≥300 bot commits / **edge-function API integration**). The edge signal (added 2026-07-15): `package.json` only sees SDK deps, but the platform proxies third-party APIs (Stripe/Finnhub/Resend/OpenAI…) through Supabase edge functions with dashboard-managed secrets — no dep fingerprint. `edge_api_integration()` reads `supabase/functions/**` bodies for a non-the platform external `fetch` host or a curated vendor secret (the platform's own AI gateway does NOT count); probed lazily only in the B branch. Probe of 407 B_light: 0.7% had committed keys but ~32% call a real non-the platform service — those were being mislabeled `B_light` and routed out.
   - `C_mover` / `C_mover_fresh` — human-led now; bot silent 0–45d, or fresh repo (<30d) carrying migrated history. Substance floor: ≥10 commits or ≥3 active days (backtest surfaced 2–6-commit shells without it).
   - Worklist order (all segments, one key): segment rank (B > A > C), then **trend** (increasing > new > steady > dropping > unknown), then lifetime bot commits, then name — the file order IS the outreach order and the dashboard renders it as-is.
   - Routed (kept in store, not in worklist): `shadow_graduate`, `presumed_silent_graduate`, `B_light`, `B_under_60d`, `C_thin`, `other`.
9. **Contact pass** (qualified rows): profile email/X/blog → local-git commit emails (hardened denylist) → README/homepage live URL; `--scrape-sites` adds a Firecrawl JS-rendered scrape (many product sites block plain HTTP). Worklist admits rows with an email or X handle; the rest stay in the store.
10. **Trend** (B rows): bot commits last 4wk vs prior 8wk via two Link-trick counts. ≥1.3× increasing / ≤0.7× dropping / else steady; <8 commits → sparse.

## First-week evidence (backtest 2026-07-06, week of Jun 29 – Jul 5)
- Funnel: 3,521 pushed → 1,487 UUID-verified → 164 marker graduates → **1,319 marker-less pool**.
- Prevalence (random 250-arm, scaled): ~285 B_all_bot, ~74 C, ~42 A per week; ~13% shadow graduates; ~11% silent graduates.
- B trend distribution: 28 dropping / 16 increasing / 11 steady / 5 sparse (top-60 by volume).
- Contactability (149 B rows, all layers): **44% email, 50% any path**. Layers: commit emails 42, site scrape 18 (10 needed JS rendering), profile 7, X/social 8. Events-API: 0/149. **Apollo: 0/35 matches (control probe fine) — this cohort does not exist in B2B sales-intelligence databases.** GitHub + their own product sites are the only contact surfaces.
- Falsifiers (pre-registered): shadow contamination in sample 0% (<50% ✓), contactability 60% in stratified sample (≥35% ✓), streams disjoint ✓.
- Seeded worklist: 72 rows (53 B / 10 A / 9 C), 70 with email.

## Known limits / next knobs
- Volume-first B ordering over-selects team/agency anomalies (a 3,394-commit week ≈ 485 prompts/day); sustained >200/wk deserves an agency flag. Trend-first ordering is the default for outreach.
- No LLM region pass yet — worklist carries `owner_location` + email TLD; when promoted, feed the region classifier commits/README (there are no marker files here; see region memory note).
- `daily_poll.py` and `ceiling_poll.py` run the same search+raw fetches independently (~double pull cost, accepted for decoupling); merge into one pull if cost ever matters.
- Boomerang re-classification inside the graduate stream (marker + bot resumed = "tried and retreated") is counted (14/26 hybrids) but not yet surfaced as its own outreach angle.

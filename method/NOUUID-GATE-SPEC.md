# SPEC — Recover the `no_uuid` pool (soften the the platform-origin gate)

_Drafted 2026-07-16. Status: REVIEWED (3-reviewer pass, 2026-07-16) — ready to implement on a branch.
See "Review pass" at the bottom for what the reviewers changed._

## The finding (why this matters)

Discovery volume is supply-limited at ~3–4 sendable/day. Root cause investigation (this session)
found the single biggest leak is **our own origin gate**, not the search breadth:

- Discovery searches `"lovable.dev/projects" in:readme fork:false` ([ceiling_poll.py:256](../pipeline/ceiling_poll.py)),
  then `g_readme` ([~ceiling_poll.py:286](../pipeline/ceiling_poll.py)) **drops the repo unless the README
  also matches the strict regex** `lovable\.dev/projects/[0-9a-f]{8}-[0-9a-f]{4}` (the full editor link
  with a UUID). Everything else → `gate_status="no_uuid"`, dropped.
- **Measured (reliable, cheap checks):** of the top 80 current-search hits, **48 (60%) fail the UUID
  gate**. Of those 48, **48/48 (100%) are genuinely the platform** (carry `lovable-tagger` in package.json).
  And **96% are un-graduated** by the marker-file test — i.e. real pre-grad builders. (Note: 96% is
  marker-file-only; it excludes the ~13% shadow + ~11% silent graduates that `score()`/`enrich_B`
  still route out, so the effective send yield is materially below 96%.)
- **Partial (rate-limit-blocked) scoring** of the `no_uuid`-real pool suggested ~40% score `B_all_bot`
  (serious). **This number is NOT confirmed and is likely high** — the comparable backtested figure is
  ~22% B_all_bot of the marker-less pool (CEILING.md). See Confirmation: do NOT use 40% as the go/no-go bar.

So the gate is throwing away the majority of the real, un-graduated platform-origin repos the search already
finds. This is the highest-ROI discovery lever. (By contrast, `lovable.app`-only and badge-stripped
buckets skew ~44% ALREADY-GRADUATED + toys — low value for the ceiling send stream; do NOT prioritize.)

## Goal

Admit `no_uuid`-real repos (real the platform, reformatted/newer badge) into scoring, gated on a reliable
the platform-origin proof, WITHOUT (a) admitting search noise, (b) tripping GitHub's secondary rate limit
or starving the live hourly poller, or (c) changing send-side behavior.

## Design

**Origin proof today** = README UUID only. **Add** these as alternative proofs of the platform origin,
checked only on the no-UUID branch:
1. `lovable-tagger` in package.json **deps/devDependencies** (the platform's Vite plugin — definitive,
   build-level). **Parse** package.json (reuse `g_pkg`'s idiom) and test membership — do NOT substring
   the raw file (a "how to remove lovable-tagger" tutorial contains the literal string).
2. `gpteng` (i.e. `cdn.gpteng.co` / `gpteng.co`) in `index.html` (the engine CDN, injected into the
   built app). Substring is safe here because it is **scoped to the repo's own index.html**, not a
   repo-wide grep. Only fetched when proof #1 fails.
3. ~~`*.lovable.app` README URL~~ **DROPPED** (was "optional/weaker"). A `lovable.app` URL in a README
   proves someone deployed *a* the platform app, not that *this repo* is it — it is exactly the signal of the
   ~44%-already-graduated bucket. Do not implement it.

**Extract a testable helper (required).** `g_readme`/`g_pkg`/`g_contents` are inner functions of
`free_gates` and cannot be imported or monkeypatched, so today there is no offline seam to unit-test the
gate (same gap `assign_segment` was extracted from `score()` to close). Add a module-level, **raise-safe**
helper:

```
def lovable_origin(txt, full):
    """Origin proof for a search hit. Returns (origin, pkg_text):
      origin: "uuid" | "tagger" | "gpteng"  (proof found)
            | ""                            (definitively no proof: all fetches resolved, none matched)
            | None                          (transient fetch error: undetermined -> caller tags proof_err)
      pkg_text: the fetched package.json string (for g_pkg reuse) or None.
    MUST NEVER raise (runs in the g_readme thread pool)."""
```

**The g_readme edit (both paths share the harvest + `pending2`):**

```
if UUID.search(txt):
    r["origin"]="uuid"
else:
    origin,pj = lovable_origin(txt, r["owner_repo"])   # pj cached below to avoid g_pkg refetch
    if origin is None: r["gate_status"]="proof_err"; return   # transient — un-stored, re-enters next run
    if not origin:     r["gate_status"]="no_uuid";  return
    r["origin"]=origin
    if pj: pkg_cache[r["repo_id"]]=pj                  # free_gates-local dict; NOT a row key (no CSV leak)
urls=[u.rstrip(".,)") for u in URL_RE.findall(txt) if not any(s in u for s in SKIP_URL)]
r["readme_urls"]=";".join(dict.fromkeys(urls))[:300]; r["gate_status"]="pending2"
```

**Invariant (BLOCKER if violated):** the proof branch sets `gate_status="pending2"` — **identical** to
the UUID branch — so admitted repos pass through `g_pkg` (native drop) and `g_contents` (marker split /
`already_tooled_other`) unchanged, and only differ from UUID repos by the `origin` tag. It must NOT set
`PASS` or score directly: `g_contents` is the only gate that routes marker-bearing graduates out, and
`score()`'s agent flag does **not** catch marker-only graduates (CEILING.md: 15/26 lacked agent trailers
pre-marker). Get this wrong and it fails OPEN into unrecoverable wrong-audience sends.

**Avoid the double package.json fetch** with a `free_gates`-local `pkg_cache = {repo_id: text}` populated
in `g_readme` and consumed in `g_pkg` (`pj = pkg_cache.pop(r["repo_id"], None) or raw(...)`). Kept OFF the
row dict so it can never leak into the run-artifact CSV (`allfields` unions all row keys). Distinct
repo_id per thread → no cross-thread key collision.

**Add `"origin":""` to the row dict** at construction (~line 285) so the CSV column is stable and the
before/after partition (below) is clean.

**Keep everything downstream identical** — admitted repos flow through the same score() → segment →
contact → worklist → send pipeline. Graduated ones still route to `marker_graduate` / `shadow_graduate`;
toys/native still route out. No send-side change.

## Coupled fix (reviewer-surfaced; softening makes it reachable)

`assign_segment` ([~ceiling_poll.py:131](../pipeline/ceiling_poll.py)):
`if c30_local>=2 and created_age<30 and bot<=5: return "C_mover_fresh"`. `bot<=5` **includes bot==0** —
a fresh, hand-committed repo with **no the platform-bot history** lands in `C_mover_fresh` (a qualified/
worklisted segment). Today the UUID gate blocks it (UUID-verified repos have bot commits in practice);
softening origin makes a **zero-bot** repo (hand-pushed clone / fixture carrying tagger) reachable.
**Fix:** add `and bot>0` — matches the segment's own semantics ("fresh repo carrying *migrated* history",
and migrated the platform history has bot commits). Low blast radius: `test_scoring.py` exercises
`assign_segment` only with `bot ∈ {10,20,500}` (all >0), so no test breaks. Ships as its **own commit**
(independently revertable). Note: `C_mover_fresh` is currently discovered/worklisted but NOT in
send_outreach's enabled segments, so this is precautionary, not a live send hole today.

## Rate-limit controls (required before merge)

1. **Per-run score cap.** Add `--score-cap N` (default **200**). After `netnew` is selected and **before**
   `score()`, sort `netnew` by `pushed_at` **ASC** (repos nearest to aging out of the `--since` window
   score first — a naive head-slice on search order silently starves the tail until it drops out of the
   2-day window) and take `[:cap]`, logging the deferred count. Deferred repos are un-stored → they
   re-appear and score next run, draining the one-time backlog over 2–3 hourly runs. `--since=yesterday`
   bounds the *window* but the first post-change run still scores the entire never-before-seen admitted
   population in one burst — the cap is what flattens that burst under GitHub's secondary limit.
2. **Per-phase call counters.** Increment a global dict inside `api()` and `raw()` (by phase label),
   print at end of run. Turns the dry-run into a ground-truth call-budget measurement (core vs raw),
   not an inference — needed before trusting a live token.
3. **STORE-AWARE gating: DEFER.** It would cut the recurring re-gate cost but does nothing for the
   persistent-noise `no_uuid` (never stored), requires reordering `store_load()` before `free_gates`, and
   risks the deliberate "re-gate everything every run to catch marker-appearance" semantic. Recurring core
   (~1,100/run) is far under 5,000/hr — optimization, not guardrail.

## Confirmation (replaces the manual scoring the rate limit blocked)

Do NOT hand-score repos (secondary rate limit + live-poller bursts make it unreliable — cost hours last
session). Instead, **one dry-run on the branch, partitioned by `origin`** (drift-free — the `uuid`
partition is a built-in control measured in the same run/population/rate-limit state; strictly better
than a two-run before/after which suffers population drift):

1. Run `ceiling_poll.py --dry-run --no-contact --score-cap <large>` on a recent `--since` window, **in the
   gap after a live poll** (watch `outreach/send/chain.log` for "cycle done" following "discover
   (ceiling_poll)"; the live poll is throttled by `.last_poll` to ≤ hourly). Do **NOT** `touch .last_poll`
   (that suppresses the next live discovery). A separate GitHub token is safest; the `.last_poll` gap with
   `--no-contact` is acceptable.
2. From the run-artifact CSV (`data/ceiling_daily_*.csv`, written even in `--dry-run`) partition rows by
   `origin`:
   - **Additive check:** every `tagger`/`gpteng` row was previously `no_uuid` (pure recovery, no change to
     the `uuid` partition's segment mix — that proves the UUID path is unregressed).
   - **Segment mix:** read the **post-`enrich_B`** segment of the `tagger`/`gpteng` PASS rows (the `[s]
     segments` line is post-enrich for the B split). Anchor success on **additive real B/A/C volume** and
     **mix vs the ~22% B backtest baseline** — NOT the unvalidated 40%.
   - **Graduate routing:** read the **`[gates]` gate_status counter** (or the CSV), NOT `[s] segments` —
     `marker_graduate`/`already_tooled_other` are `gate_routed` and never appear in `netnew`/`[s]`.
     Confirm the `tagger`/`gpteng` partition routes its marker-bearing repos to `marker_graduate` and its
     agent-trailer repos to `shadow_graduate` (both out of the send set).
   - **`score_err` reconciliation:** the AFTER population scores ~2× repos; under secondary-limit pressure
     `score()` fails to `score_err` (no segment), silently deflating serious%. If `score_err` in the
     `tagger` partition ≫ the `uuid` partition, the mix is untrustworthy — re-run in a cleaner window.
3. **API cost:** read the per-phase counters for core vs raw, first-run (backlog) and a representative
   window. Confirm no single run approaches 5,000 core/hr and the cap flattens the first-run burst.
4. Optional loose sanity cross-check only: a literal before/after `[s]` diff (main vs branch). Not the
   primary measure (population drift).

## Dry-run confirmation — measured 2026-07-16 (branch e1c2c1f)

`ceiling_poll.py --dry-run --no-contact --score-cap 300`, `--since=yesterday` (2-day window, pool 2129),
run in the live-poll gap. Same window a live poll had just reported `no_uuid=1230`. All PASS.

- **Recovery (huge, as predicted):** `no_uuid` **1230 → 69**; `pending2` (repos reaching the deeper gates)
  **~866 → 2043**. Origin partition: **tagger 1169**, uuid 874, none 86 (69 no_uuid + 17 junk).
  **gpteng caught 0 unique** — `lovable-tagger` alone accounts for 100% of the recovery this window.
- **Graduate routing WORKS (the BLOCKER, confirmed on real data):** of the 1169 tagger-admits, **149 routed
  OUT to `marker_graduate`** at the gate (+2 `already_tooled_other`); among the scored sample, shadow+silent
  graduates (43+49 = 92, 31%) routed out by `score()`. **No graduate leaked into a send segment.** The
  tagger pool is *cleaner* on graduates than the uuid pool (12.7% vs 17.4% marker_graduate).
- **Segment mix (300 oldest-pushed scored; `score_err=0`, so not rate-limit-deflated):** B_all_bot 133 (44%),
  A_hybrid 22, C_mover 2 → **sendable A/B/C 157 (52%)**; routed out 143 (48%: shadow 43, silent 49, B_light 9,
  B_under_60d 3, C_thin 1, other 38). **Caveat:** the cap orders `pushed_at` ASC, so the sample skews to
  older repos and likely *overstates* the serious%; the true rate across all 1018 tagger-PASS admits is
  unmeasured. Even at the conservative ~22% B baseline that is **~220 B_all_bot per 2-day window vs ~2/run
  today** — the supply ceiling is broken (system becomes send-cap-limited, not supply-limited).
- **API budget (ground-truth counters):** **3224 core / 4324 raw** total. `gate:contents=2042 core`
  (recurring, uncapped — the new floor, ~2.4× baseline), `gate:readme=3419 raw`, `gate:pkg=881 raw`,
  `score=612 core` (300 capped), `enrich_B=544 core`, `pull=26`. **The cap is load-bearing:** uncapped
  (scoring all 1033 net-new) extrapolates to ~5,950 core → **over the 5,000/hr limit**; capped at 300 →
  3,224 core, comfortably under. Steady-state (backlog drained, `score()` small) ≈ ~2,200 core/run, floored
  by the uncapped `g_contents`. The dry-run stayed inside the gap and did not starve the live poll.
- **Net:** additive, high-quality, graduates route out, cost under budget with the cap. Operational cost is
  real but bounded: runs get ~2× wall-clock (raws) and ~2.4× g_contents core. If that friction bites, the
  deferred **store-aware gating** is the fast-follow (it removes the recurring re-gate of already-stored
  admits). `gpteng`/index.html can be dropped (0 unique catches) or kept as ~free insurance.

### Send-funnel (measured 2026-07-16, contact-enabled dry-run)

A second dry-run (`--score-cap 200 --since <today>`, contact ON, `--scrape-sites` **off**) pushed the
recovered pool through the actual send filters — importing `send_outreach.region_class`/`is_role` for a
faithful replica — to turn discovery volume into **sendable-with-email-in-geo/day**:

- **1-day discovery:** 1,408 hits → **690 net-new PASS** (vs ~105 under the old UUID gate — **~6×**).
- **Send funnel on 200 scored:** 101 sendable-segment (50%) → **16 have a GitHub-native email (15%)** →
  15 not-role → **14 pass geo** (region dropped only **1 nontarget**; 13 unknown kept, 1 target) → 14 dedup.
  = **14/200 (7%)**. Sample survivors are personal gmail/icloud on real products.
- **The binding filter is EMAIL FINDABILITY, not geo.** Geo drops ~7% (the *keep-unknown* policy means empty
  GitHub locations pass). The **15% email rate is a FLOOR** — this run skipped `--scrape-sites`; the live
  poller runs it, and the app-scrape lifts contactability to ~44% (backtest), ~3× the floor. 85% of these
  builders have no email *on GitHub* — the site-scrape is load-bearing for reach.
- **Daily sendable:** **~40-50/day at the floor, ~100-130/day with the live scrape** → **saturates
  `daily_cap` 100**. The system flips from supply-limited (~4/day) to **send-cap-limited** — the point of the
  change. (~690/day is first-run backlog; steady-state daily *new* is lower but still clears the floor.)
- Run cost: 2,253 core / 2,842 raw (1-day, contact on).

## Risks (reviewer-corrected)

1. **Token-burst concurrency (THE biggest operational risk).** No single poll breaches 5,000 core/hr
   (first-run peak ~2,750 core with backlog, steady ~1,100). The failure mode is the **branch dry-run**
   (or an ad-hoc `daily_poll.py`) overlapping the **live hourly poll** on the shared `gh auth token`,
   tripping GitHub's *secondary* (burst) limit; `api()` self-heals (403/429 → backoff) so the live poll
   **slows** rather than fails, but a slowed poll can push `send_outreach.py` past a recipient's tz send
   window. Controls: the per-run cap, dry-run in the `.last_poll` gap / separate token, and don't run
   `daily_poll` on rollout day.
2. **Cost model (corrected).** The extra package.json `raw()` is paid by the **whole no_uuid pool every
   run** (never stored → re-gated), not once — ~+570 raws/run (raw.githubusercontent, separate host, own
   429 backoff; absorbed by the ~55-min poll slack). The `score()` doubling is a **one-time ~300–550
   first-run backlog** (then stored+skipped) + a recurring **~+450 core/run** `g_contents` gate cost on
   re-gated admits. The spec's earlier "~700 → ~1,400/run doubling" was wrong (conflated the cumulative
   store `B_all_bot` count with per-run scoring).
3. **False positives.** Parsed `lovable-tagger` and index.html-scoped `gpteng` are definitive; the
   dropped `*.lovable.app`-URL proof was the noise vector. `SKIP` junk-drop runs first (but only matches
   full_name+description, so it won't catch a neutrally-named tutorial with the platform *content* — hence
   build-level proof, not README mention).
4. **Graduate leakage.** The marker split (`g_contents`) + shadow/PSG screens run unchanged on admits;
   the admitted pool is *cleaner* on markers than the UUID pool (2/48 vs 164/1,487). Low net-new risk —
   but reconfirm via the `[gates]` counter (Confirmation §2).
5. **Volume surge on one un-warmed Gmail.** Send throttle (daily_cap 100, ~45/day steady, tz-window,
   6% bounce-halt) fully absorbs it — the worklist just deepens (leads don't expire). No discovery-side
   throttle needed; the only send concern is cold-sender reputation, governed by keeping daily_cap
   conservative.
6. **Blast radius of the gate refactor.** The g_readme change touches only the no-UUID branch; the
   extracted `lovable_origin` must be raise-safe (parse under try/except + isinstance guard) and the
   `pending2` invariant must hold (Design). Unit tests (below) pin the UUID path and every branch.

## Required tests (test_scoring.py style — monkeypatch `cp.raw`/`cp.api`, no network)

Against module-level `lovable_origin(txt, full)` and the g_readme wiring:
1. UUID in txt → `("uuid", None)` (UUID-path regression guard).
2. No UUID, package.json parses with `lovable-tagger` in deps/devDeps → `("tagger", <text>)`.
3. No UUID, real package.json WITHOUT `lovable-tagger`, index.html without gpteng → `("", ...)` → `no_uuid`.
4. No UUID, package.json `raw()` → `None` (transient), index.html `None` → `(None, None)` → caller tags `proof_err` (not `no_uuid`).
5. No UUID, package.json `""` (404), no gpteng → `("", ...)` → `no_uuid`.
6. Raise-safety: malformed JSON containing the substring `lovable-tagger` → NOT `"tagger"` (parse-based, so `("", ...)`) and does not raise; `raw()` raising is caught (mirror the edge `"raw() raising -> caught"` test).
7. No UUID, no tagger, index.html contains `gpteng` → `("gpteng", ...)`.
8. readme_urls harvest + `pending2` on a proof-admit (URL-bearing UUID-less README + tagger) → `readme_urls` populated, `gate_status=="pending2"`, `origin=="tagger"`.
9. `pkg_cache` reuse: g_pkg consumes the cached text without a second `raw(package.json)`; the cache never appears as a row key (no CSV leak).
10. `assign_segment` `C_mover_fresh` with `bot==0` → NOT `C_mover_fresh` (the coupled `bot>0` fix); with `bot>0` unchanged.

## Rollout
Branch → implement (module-level `lovable_origin`, `pending2` invariant, cap, counters, `bot>0`) with
tests green → `--dry-run` origin-partition confirmation (segment mix vs ~22% baseline, graduate routing
via `[gates]`, `score_err` reconciled, per-phase call budget) → if additive/mix/cost pass, **recommend**
merge to the operator → on merge, monitor a day (segment mix, per-run core/raw, send volume, bounce rate).
**Kill-switch:** a module-level `ORIGIN_PROOF=True` constant (default on) so the softening can be disabled
in the live checkout without a revert, mirroring the config.json overlay pattern.

## Key files
- `pipeline/ceiling_poll.py` — `pull()` (search), `free_gates`/`g_readme`/`g_pkg`/`g_contents` (the gate),
  `lovable_origin` (new), `assign_segment` (coupled `bot>0`), `score()`, `run()` (cap), `api()`/`raw()`
  (counters).
- `pipeline/test_scoring.py` — the offline test gate (extend with the 10 cases above).
- Confirmation: `ceiling_poll.py --dry-run --no-contact --score-cap <large>` + the run-artifact CSV.
- Related memory: `discovery-volume-ceiling`, `outreach-autosend`, `region-classify-needs-marker-text`,
  `github-backup-and-dashboards`.

## Review pass (3 reviewers, 2026-07-16) — what changed
- **Correctness/blast-radius:** added the `pending2` invariant (BLOCKER), the raise-safe module-level
  `lovable_origin` extraction, the `proof_err` transient status, and the `origin`-column init.
- **Operational/rate-limit:** corrected the cost model (one-time backlog + recurring gate cost, not
  700→1,400); identified token-burst concurrency as the #1 risk; added the per-run score cap (200,
  `pushed_at` ASC) and per-phase call counters; deferred store-aware gating; specified collision-safe
  dry-run scheduling; confirmed the send side absorbs the surge.
- **Data-quality/simpler-alt:** dropped the `*.lovable.app` URL proof; made the tagger check parse-based;
  kept index.html-scoped `gpteng`; added the coupled `C_mover_fresh bot>0` fix; re-anchored success off
  the unvalidated 40% onto additive-volume + ~22% baseline + graduate routing; fixed the confirmation to
  read graduate routing from `[gates]` (not `[s]`) and to reconcile `score_err`.

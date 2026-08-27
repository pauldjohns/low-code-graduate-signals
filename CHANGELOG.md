# Changelog

The arc of the the platform-graduate outreach system: from lead discovery to a live, self-running
cold-email engine with segment-level targeting and conversion tracking. Dates are the work dates.

Format loosely follows Keep a Changelog. Newest first.

---

## 2026-07-27 — Verify before sending; the automatic halt comes out

Two changes that move bounce control from *after* the send to *before* it. a configuration choice, following
the Review campaign's implementation; the port notes are in `method/VERIFY-INTEGRATION.md`.

- **Bouncer mailbox verification, as its own chain step.** `validate_emails.check()` proves a
  *domain* accepts mail and says nothing about whether the *mailbox* exists — on the Review campaign
  it passed 457/457 addresses and 11.1% of what it cleared hard-bounced. New `pipeline/verify_queue.py`
  (+ the ~10-line `verify_bouncer.py` adapter) closes that gap, wired into `run_chain.sh` after
  discovery and **before** the send. Deliberately *not* folded into
  `ceiling_poll`: verification inside the sourcing transaction means a mid-run vendor error writes
  `status=skipped` across the batch and locks those rows out permanently (a real drafting error
  caught in the Review repo).
  - **Only `undeliverable` is terminal.** `risky`, `unknown`, and deliverable-on-an-accept-all domain
    are recorded and stay sendable, so the harder rules get set from this campaign's own bounce
    outcomes rather than a 40-row sample. An unrecognised vendor status records as itself, never culls.
  - **A vendor error is not a verdict** — on 402/401/timeout the run stops rather than spending the
    batch re-learning the same failure; rows stay unverified and the next cycle retries them.
  - **Cull ceiling**: >40% undeliverable in a batch (min 20 rows) aborts and writes nothing. That is
    a bad key, a vendor incident or a changed status vocabulary, not a bad list.
  - Ships `verify_enabled:false`. One `--apply --limit 50` pass gets reviewed by hand first.
  - `verify_status` / `verify_reason` / `verify_date` added to `DATA_COLS` in `ceiling_poll.py`
    **before** anything else — `worklist_upsert` rewrites the file with `extrasaction="ignore"`, so a
    column outside that list is silently dropped on the next poll. This is what made `email_check`
    repeatedly vanish in the Review repo; `pipeline/test_worklist_columns.py` is the regression test.
  - No `verify_cull_esps` here: this worklist has no `esp` column, and GitHub sourcing yields
    personal/freemail addresses rather than corporate gateways. Shipping the branch would be inert code.

- **Circuit breaker disabled (`breaker_enabled:false`), replaced by an adaptive throttle.** The
  breaker flipped a switch on a trailing rate whose confidence interval was far wider than the gap
  between "fine" and "stop", and it **deadlocks by construction**: `breaker_reason()` runs before any
  send, so while halted the trailing window is frozen and no clean send can dilute it — clearing
  `HALT` re-trips at any elapsed time (confirmed 07-18 → 07-19: sender down ~22h, window still
  7/100). What replaces it scales *volume* instead: ≥4% → 75% of cap, ≥7% → 50%, ≥12% → 25%,
  ≥20% → stop, evaluated only once the window holds 40 sends. Reputation harm scales with volume, so
  throttling is the proportionate response. The 7% rate that stopped the campaign outright now halves
  the cap. Bounces are still scanned and hard-bounced addresses still suppressed — what is gone is
  *bounce-rate-driven halting* specifically. `HALT` (auth-fatal guard, throttle stop tier), the daily
  cap, the timezone window, suppression and the role/region/segment filters all remain automatic.
  `bounce_*` keys kept but inert, so rollback is one edit. **This is the committed default; the live
  machine runs an uncommitted `config.json` overlay that must have `breaker_enabled:false` added by
  hand, or the breaker stays armed there with nothing in the log saying so.**
- **On "changing the bounce window":** the *committed* `bounce_window` has been 100 since `31e9038` and
  is unchanged here — the 120 lives only in the uncommitted live overlay on the operator's machine, which this
  commit cannot touch. With `breaker_enabled:false` that key is inert either way. The window that now
  matters is `throttle_window` (100, trailing live sends), with `throttle_min_n` (40) as the minimum
  sample before any tier fires.

**Fixed during review** (three independent reviewers; all CONFIRMED against the code):
- The mid-run cap re-check used the raw `daily_cap`, not the throttled `effective_cap`, so after
  selection a throttled run could keep sending to the unthrottled ceiling for the rest of a
  multi-hour pass. The throttle was advisory-only past `select()`.
- The throttle's stop tier returned with a `print` and no on-disk marker — a permanent stop that is
  strictly *less* observable than the breaker it replaces, since it freezes the trailing window the
  same way. It now writes `HALT`, which `run_chain.sh` echoes every cycle.
- `adaptive_throttle` disabled silently on any non-literal-`true` value and returned an empty note
  that the caller then suppressed. One config typo could have left a live cold sender with no
  bounce-driven control and no line in `chain.log` saying so. The note is now never empty.
- `verify_queue.sweep()` rewrote a worklist snapshot taken *before* a multi-minute API window,
  silently reverting any `status=sent` / `contacted_on` / `notes` edit that landed in between. It now
  re-reads and merges by email, and takes the sender's `flock` (VERIFY-INTEGRATION.md §7 step 5 tells
  the operator to run `--apply` by hand, which takes no chain lock).
- A chronically-`unknown` address was re-billed every poll cycle forever — `verify_date` was written
  and read nowhere. Now a 7-day cooldown.
- `[-0:]` slices the WHOLE log, not an empty window (`throttle_window`/`bounce_window` of 0), a blank
  email in `bounces.csv` matched every send with a blank recipient, and `throttle_min_n:0` on an
  empty log raised `ZeroDivisionError`. All guarded.
- Added `require_verified` (default false): "nothing is mailed unverified" was true only by the
  accident that the worklist sort puts the sender's first rows inside `verify_max_per_run`. The flag
  makes it structural.
- Corrected three false claims in the docs: `STOP` is *not* the only hard brake (HALT, the auth-fatal
  guard and the throttle stop tier all remain automatic), bounce rate is *not* on the dashboard
  (`build_metrics.py` renders Sent/Replies/Accounts only), and the AUTOSEND architecture diagram had
  `select` above the breaker/throttle when the code order is the reverse.

46 new offline tests (28 verify · 14 throttle · 4 column-survival); full suite green.

---

## 2026-07-18 — Role filter: exact-match only, plus machine identities

`community@teamsite.com` hard-bounced, taking the breaker window to 5/100 against a 6% threshold,
and `ai@toolhub.com` / `dev@campusapp.app` were sent in error the same morning. All three were
missed by the 07-17 tightening. Rather than extend the list a third time, the filter was measured
against every address the project has ever seen (679).

- **Removed the first-token match.** `is_role()` matched the first `._-`-delimited token as well as
  the whole local. Over the full 679-address history that clause caught **0 role inboxes and 2 real
  people** — `mail.to.sample@gmail.com` and `contato.sampledev@gmail.com`, both sitting in the live worklist as `status=skipped`. Its only justifying test fixtures
  (`seo.team@`, `marketing-eu@`) were synthetic, written in the same commit as the rule. All 77 real
  role hits matched on the whole local anyway. Both leads were released back into the queue.
- **Added `BOT_LOCALS` + `BOT_DOMAINS`** for machine git-author identities. `agent@antigravity.ai`,
  which tripped the 07-17 breaker, was mis-filed as a scraped contact-page address; it is actually a
  coding agent's commit identity. A queue audit found `codex@openai.com` queued twice and
  `fix@claude.ai` once, all sendable. `BOT_DOMAINS` covers product/bot domains only, never employer
  domains — a real person at `@openai.com` stays a valid lead. This class grows as the target
  population adopts more agents.
- **Net across all 679 known addresses:** 16 newly filtered (verified individually as role or
  machine), 2 released (both real people). Role filtering now uses one set and one matching rule.
- **Queue swept** (`queue_cleanup.py`): 13 rows marked `skipped` across two passes — 3 machine
  identities, 10 role/department inboxes. MX and syntax validation over all 331 distinct pending
  addresses returned zero failures, so no dead domains are queued.
- **Docs corrected.** `method/AUTOSEND.md` claimed the breaker trips on "any complaint" and measured
  over a trailing 50. Neither was true: no complaint signal exists anywhere in the pipeline, and
  `bounce_window` is 100. The role filter was undocumented entirely.

**Not addressed, deliberately** — flagged for a decision, not changed here: `bounce_rate_halt` at 6%
is well above cold-outreach norms and false-trips ~12% of windows at the observed ~3% rate; the
sending domain's DKIM record was malformed (a console label pasted into the DNS TXT value, so
forwarded mail arrived unauthenticated - check yours); and outreach still sends from the company's
primary mail domain.

## 2026-07-15 — Edge-function seriousness signal + segment expansion

The sender had drained: only `B_all_bot` was enabled and its addressable pool was down to ~4.

- **Root cause found.** The seriousness gate read only `package.json` deps and root folder names.
  the platform proxies third-party APIs (Stripe, Finnhub, Resend, OpenAI…) through Supabase edge
  functions with dashboard-managed secrets, so a real integration leaves no `package.json`
  fingerprint. A probe of 407 `B_light` repos: 0.7% had committed API keys, but 47.9% had edge
  functions and ~32% called a real non-the platform service — all mislabeled `B_light` and routed out.
- **Added** `edge_api_integration()` (`ceiling_poll.py`): reads `supabase/functions/**` bodies for a
  non-the platform external `fetch` host or a curated vendor secret. the platform's own AI gateway does not
  count. Wired into a pure, testable `assign_segment()` cascade, probed lazily only in the B branch
  so no other segment can be perturbed. Exception-safe (runs in the unguarded scoring pool).
- **Fixed a live double-send bug.** `select()` deduped against the send log but not against emails
  already picked in the same run, so a founder with multiple repos got emailed more than once
  (observed live: one address 3× in a single run). Now dedups by email within the run.
- **Enabled segments** widened from `[B_all_bot]` to `[B_all_bot, A_hybrid, C_mover,
  presumed_silent_graduate]`; addressable pool ~4 → ~145.
- **`presumed_silent_graduate` activated as a one-shot cohort:** 461 sendable → 63 with a real
  edge-function integration → junk/placeholder-email filtered → email-deduped → 15 staged live,
  ~46 held back pending a batch-1 bounce check (`outreach/send/psg_holdback.csv`,
  `pipeline/psg_stage_write.py`).
- Three independent review agents pressure-tested the plan before merge; unit tests added
  (`test_scoring.py`, `test_send_dedup.py`). Plan and reviews live-verified against the running poller.

## 2026-07-08 — Timezone scheduler, region/role filters, 24/7 service, go-live

- **Timezone scheduler:** deliver each recipient in their local 08:00–11:00 window; stateless
  per-recipient hash offset spreads sends across the window (no queue). DST via `zoneinfo`.
- **Region gate:** target US/Canada/UK/Europe; keep unknown, drop positively non-target.
- **Role-address filter:** skip shared inboxes (`sales@`, `info@`, incl. PT/ES/DE locals).
- **24/7 service:** keep-awake launchd agent + a detached `nohup` engine loop started from a
  Terminal grant (macOS TCC blocks launchd from `~/Documents`). `go.sh` / `stop.sh` controls.
- **Copy/landing:** `ceiling_b` template with `{{app_name}}` merge field, landing at
  `https://dev.your-domain.example`.
- Two review passes; 8 review findings fixed (tz/lock majors + coverage).

## 2026-07-07 — Auto-send engine (dry-run, review-hardened)

- Hand-rolled Gmail sender (API + OAuth) from `sender@example.com`. Ships dry-run; three
  deliberate config flips to go live. Write-ahead send log (no double-send), flock, STOP/HALT,
  circuit breaker (bounce rate), daily cap, per-send jitter.
- Cheap pre-send email validation (syntax + MX + disposable).
- Ceiling backfill 2026-06-09..07-07: +266 leads (worklist 338), store 4,657.
- `method/AUTOSEND.md` canonical spec.

## 2026-07-06 — Ceiling stream (pre-graduation builders)

- Inverse of the graduate poll: find platform-origin repos with no pro-tool marker that show
  readiness signals, before they graduate. Deterministic, cron-safe.
- Segments `A_hybrid` / `B_all_bot` / `B_light` / `C_mover(_fresh)` / `shadow_graduate` /
  `presumed_silent_graduate`; dedup store keyed by repo id; append-only worklist upsert.
- Wired into the dashboard + tracking. `method/CEILING.md` docs. Two-agent review fixes.

## 2026-06-29 → 07-02 — Lead system foundation

- Repo + social lead pipelines, worklists, filterable checkbox-tracked dashboard (file + live-edit
  server modes). Post-age staleness for social leads (HN ~2wk, Reddit ~2mo).
- Gmail tracker: sync `worklist_repo` sends/replies/delivery-delay.
- Daily pulls of repo graduates + social warm leads.

---

_Contact PII (lead emails/names) lives only in the local repo snapshot; see `.gitignore`. This
changelog and the code are the shareable record of the arc._

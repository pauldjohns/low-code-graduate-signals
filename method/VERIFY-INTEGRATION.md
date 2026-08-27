# Mailbox verification for the the platform engine

*Written 2026-07-27, from the Review campaign's implementation (the sibling campaign).*

Adds a Bouncer mailbox-verification step between **sourcing** and **sending**, matching the Review
chain. Nothing here replaces an existing filter — the local checks in section 4 are suggestions for
consideration only.

---

## 1. Why a step at all

`validate_emails.check()` (already used here by [queue_cleanup.py](../pipeline/queue_cleanup.py) and
`select()` in [send_outreach.py](../pipeline/send_outreach.py)) proves a **domain** accepts mail. It
says nothing about whether the **mailbox** exists. On the Review campaign it passed 457/457 addresses
as SENDABLE and 11.1% of what it cleared hard-bounced; four of six bounces on 2026-07-21 were
mailbox-level failures. That is the gap this closes.

Source of the pattern, all in the sibling campaign's repo:

| What | File |
|---|---|
| The step itself | `pipeline/verify_queue.py` |
| Bouncer adapter (`v_bouncer`) | `pipeline/verify_bakeoff.py:103` |
| Chain wiring | `pipeline/run_chain.sh:64-72` |
| Config keys | `outreach/send/config.json` (`verify_enabled`, `verify_max_per_run`, `verify_cull_esps`) |
| Design rationale | `method/VERIFY-PLAN.md` |

## 2. The API

- `GET https://api.usebouncer.com/v1.1/email/verify?email=…&timeout=30`, header `x-api-key`.
- Key lives **outside the repo**, mode 600, in the **shared** secrets dir, alongside `apollo.env`
  and the Google OAuth files:

```
~/.config/shared-outreach/verify.env     BOUNCER_API_KEY=...
```

  **Superseded, 2026-07-27 (a configuration choice).** This section originally said to mint a second key in a
  separate `~/.config/lovable-outreach/` dir, by analogy with the split Gmail token dirs. Both
  halves of that were wrong:
  - **Bouncer credits are per-key**, so a duplicate key splits the usage accounting for no benefit.
  - The Gmail analogy does not transfer. The token dirs are split because `go.sh` runs
    `rm -f "$TOKEN"` on re-consent — and `TOKEN` is the `token.json` path specifically, so
    `verify.env` was never at risk from it.

  `verify_bouncer.load_key()` resolves `$BOUNCER_API_KEY` → `~/.config/lovable-outreach/verify.env`
  → `~/.config/shared-outreach/verify.env`, first hit wins. The engine-local path is checked first
  and does not exist, so the engines can still be split later by dropping a file in — no code
  change. The lookup must stay in Python: `run_chain.sh` invokes `verify_queue.py` directly, so a
  `set -a; . verify.env; set +a` shim in an interactive shell never reaches the headless loop.
- ~$0.008/address at the 1k tier. The dry-run prints projected spend before anything is called.

## 3. Chain placement

In [pipeline/run_chain.sh](../pipeline/run_chain.sh), inside the existing hourly-throttled poll
block, **after** `ceiling_poll.py --scrape-sites` and **before** the send:

```bash
  VERIFY_ON=$(python3 -c "import json;print(json.load(open('$SEND/config.json')).get('verify_enabled') is True)" 2>/dev/null || echo False)
  if [ "$VERIFY_ON" = "True" ]; then
    VERIFY_MAX=$(python3 -c "import json;print(int(json.load(open('$SEND/config.json')).get('verify_max_per_run',150)))" 2>/dev/null || echo 150)
    echo "[$STAMP] verify queued addresses (Bouncer, max $VERIFY_MAX)"
    python3 pipeline/verify_queue.py --apply --limit "$VERIFY_MAX" || echo "[$STAMP] verify_queue failed (continuing, rows stay unverified)"
  fi
```

Verification runs before the send, so nothing is mailed unverified. Failure is safe: rows keep a
blank `verify_status` and the next cycle retries them, which is exactly today's behavior.

Keep it as **its own step**, not part of `ceiling_poll`. In the Review repo the equivalent mistake
was drafted and caught: verification inside the sourcing transaction means a mid-run vendor error
writes `status=skipped` across the batch and locks those rows out of the campaign permanently.

## 4. Rules that matter more than the API call

1. **Only `undeliverable` is terminal.** `risky`, `unknown`, and deliverable-on-an-accept-all domain
   are recorded and stay sendable, so the harder rules get set from this campaign's own bounce
   outcomes rather than a small sample. An unrecognized vendor status records as itself, never culls.
2. **A vendor error is not a verdict.** On 402/401/timeout the run stops rather than spending the
   rest of the batch re-learning the same failure. Rows stay unverified; next cycle picks them up.
3. **Cull ceiling.** If >40% of a batch (min 20 rows) returns undeliverable, abort and write nothing.
   That is a bad key, a vendor incident, or a changed status vocabulary — not a bad list.
4. **Idempotent, and it never touches your tracking edits.** Blank-`status` rows only. A settled
   verdict with a reason on file is never re-billed; `unknown` (transient/greylist) and rows missing
   a reason are re-verified. Atomic `.tmp` + `os.replace`.

## 5. Two things that differ here, and will break a straight copy

**a. Column survival — this is the one that bites.**
[ceiling_poll.py:246](../pipeline/ceiling_poll.py:246) and `:260` rewrite the worklist with
`fieldnames=TRACK_COLS+DATA_COLS, extrasaction="ignore"`. Any `verify_status` / `verify_reason` /
`verify_date` columns added by hand are **silently dropped on the next poll**. Add the three to
`DATA_COLS` (line 33) before wiring anything else; `verify_queue.py` exits with a migration message
if they are absent. This exact failure is what made `email_check` repeatedly vanish in the Review
repo — see its `pipeline/test_worklist_columns.py` for a regression test worth porting.

**b. No `esp` column.** `verify_cull_esps` (the Mimecast/Barracuda pre-send host cull) reads
`row["esp"]`, which this worklist does not carry — GitHub sourcing yields mostly personal and
freemail addresses, not corporate gateways. Ship with `verify_cull_esps: []` and drop `_esp_culled`
and the `esps` filter argument, or the code is inert branches. Revisit only if the the platform list
starts showing gateway-hosted domains.

Also: `verify_queue.py` imports `norm` from `send_outreach` (exists here) and `v_bouncer` from
`verify_bakeoff` (does **not** exist here — port just that ~10-line adapter, the bake-off harness is
Review-specific). Rows are matched on email, which is fine — this worklist keys on `owner_repo`, and
`select()` already dedupes one email across multiple repo rows.

## 6. Local, non-API options — suggestions only, additive

Existing filters stay as they are. Each of these is a candidate addition to
[queue_cleanup.py](../pipeline/queue_cleanup.py) `bad_reason()`, in front of the paid call so it
cuts spend as well as bounces:

- **RFC 7505 null-MX handling — already present, no action needed.**
  [validate_emails.py:35](../pipeline/validate_emails.py:35) carries the 2026-07-19 `_parse_mx` fix.
  Noted so it does not get "re-fixed" or refactored away: a lone `0 .` MX answer means the domain
  accepts no mail and must not fall through to the A-record check.
- **Freemail typo list.** GitHub profile emails are hand-typed: `gmail.cm`, `gmial.com`, `gmail.con`,
  `hotmial.com`, `yahoo.co` etc. A ~20-entry static set is free and catches what MX alone will not
  when the typo domain is registered and parked.
- **GitHub noreply locals.** `…@users.noreply.github.com` — the `"noreply" in e` check already
  covers these; worth confirming it fires before any Bouncer call rather than after.
- **Per-domain result cache across runs.** `validate_emails._mx_cache` is per-process. Persisting
  domain-level verdicts to a small CSV would cut repeat DNS work on re-polls.

None of these is a substitute for the mailbox check — they are cheap pre-filters that reduce what
reaches it.

## 7. Order of work

1. Add `verify_status`, `verify_reason`, `verify_date` to `DATA_COLS` in `ceiling_poll.py`; run one
   poll; confirm the columns survive.
2. Port `verify_queue.py` + the `v_bouncer` adapter; strip the `esp` cull.
3. Add the config keys with `verify_enabled: false`.
4. Dry-run (`python3 pipeline/verify_queue.py`) — it makes no calls and prints projected spend.
5. Review one `--apply --limit 50` pass by hand before flipping `verify_enabled: true`.
6. Wire the `run_chain.sh` block last.

## 8. Honest limits on the calibration

The Review campaign culled 62 addresses as undeliverable. We never mailed them, so the
false-positive rate is unmeasured. The decision to trust Bouncer came from a bake-off against
**six** known bounces, where it caught three of the four mailbox-level failures — directional, not a
measured precision. Accept-all is unsettled: Bouncer abstained on 0% of Google rows while Apollo
flagged 46.8% of the same rows as accept-all. Copy the *recording* behavior, not a decision rule,
until this engine's own bounce-rate-by-verdict data can settle it.

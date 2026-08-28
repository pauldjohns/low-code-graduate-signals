# low-code-graduate-signals

Finds developers who built an app on a low-code app builder and are outgrowing it, then mails them
once, in their local morning, and stops if they don't answer.

Two halves. The **signal** half reads GitHub. Builders of this kind write their in-editor commits
under a single identifiable bot identity, so a repository's commit stream classifies cleanly into
platform-bot, infra-bot, web-flow and local-git-human. The shape of that mix over time says whether
someone is ramping into the platform's ceiling, has picked up a professional coding tool alongside
it, or has detached the sync entirely. The **outreach** half is a hand-rolled Gmail sender with
timezone scheduling, address verification, bounce scanning and an adaptive throttle.

The implementation is written against one platform, because you cannot classify a commit stream
without naming the bot identity that produces it. The method transfers to any builder that commits
on its users' behalf under a stable identity - which is most of them.

**No lead data is in this repo and none ever will be** - see
[What is deliberately missing](#what-is-deliberately-missing).

## The motion

```
daily_poll.py     GitHub search for platform-origin repos → marker check → graduate store
ceiling_poll.py   commit-stream classification → segment + score → pre-graduate worklist
social_pull.py    Reddit / HN / Bluesky / Mastodon / Dev.to → timing and wedge language
social_link.py    resolve a pseudonymous handle to a real identity, where it resolves at all
build_worklist.py score, segment, region-gate → the sendable list
verify_queue.py   mailbox verification before a send can spend reputation on a dead address
send_outreach.py  select → render → send via Gmail → log
bounce_scan.py    NDRs and opt-outs → bounces.csv / suppression.csv
build_metrics.py  sends, replies, signups attributed back to a repo
```

`engine_loop.sh` runs the chain every ~30 minutes; `pipeline/go.sh` arms it and
`outreach/send/STOP` is a hard brake honored mid-run.

## What is worth stealing

- **Commit-stream classification instead of file artifacts.** The obvious graduation tell – a repo
  grows its own Supabase config, CI, custom domain – tested badly: the best single artifact showed
  up before the marker only 36% of the time. Commit *identities* worked. Details, including the
  matcher bug that made an earlier pass read 2/28 where the truth was 26/26, are in
  `method/CEILING.md`.
- **Reaching people before the switch, not after.** Scoring each known graduate as of the day before
  its marker file landed, 22 of 26 were catchable in advance.
- **An honest read on social.** `method/SOCIAL.md` records that turning a pseudonymous handle into a
  contactable identity works for a small subset and is not a general bridge. Most volume is Reddit,
  where the only contact path carries brand risk for a named founder. The stream is kept for timing
  and for the words people use, not as a lead source.
- **The sending discipline.** One send per person ever, suppression on any opt-out, a throttle that
  scales the daily cap by recent bounce rate rather than a binary halt, and mail timed to the
  recipient’s morning from their own location.

## Run it

```bash
pip install -r pipeline/requirements-send.txt
cp .env.example .env      # then fill it in - it is gitignored
```

1. A GitHub personal access token (read-only, public-repo scope) as `GITHUB_TOKEN`. Without one the
   API gives you 60 calls an hour and the search half is unusable.
2. Gmail API credentials: Google Cloud project → enable the API → OAuth consent with `gmail.send`
   and `gmail.readonly` → Desktop-app client ID, saved outside the repo.
3. Optional: a Bouncer key in `~/.config/shared-outreach/verify.env` for mailbox verification.
4. Copy the files in `examples/` to the paths named in `outreach/send/config.json`, then set
   `from_address`, `reply_to` and real copy in the template, and flip `dry_run` to `false` and
   `template_approved` to `true`. **Both ship disabled.** Nothing sends until you change them.

Tests are offline. `pipeline/test_send.py` is excluded on purpose – it sends live mail:

```bash
for t in pipeline/test_*.py; do [ "$t" = "pipeline/test_send.py" ] && continue; python3 "$t" || break; done
```

## What is deliberately missing

- the worklists, lead files, seen-stores, analysis CSVs and the built dashboard – thousands of real
  people’s repo names, commit emails, locations and social handles
- the send log, bounces, suppression list and run logs
- the sending identity and all credentials

Every address, handle and repo name left in the tests and docs is synthetic. The two case studies in
`method/2026-06-24-firstpass.md` describe real repositories whose handles, project names, live
domains and verbatim file contents were replaced with placeholders – the pattern is the point, the
subjects are not.

## Before you mail anyone

A commit email in a public repository is published for a purpose, and cold outreach is not that
purpose. Under GDPR it stays personal data with all the usual duties: a lawful basis, a real
disclosure of where you got it, and erasure on request. CAN-SPAM wants accurate headers, a postal
address and a working opt-out. The engine gives you the mechanics – no re-sends, suppression on
opt-out, a hard stop – and none of the judgment.

## Layout

```
pipeline/      the signal scripts, the sender, and the offline tests
method/        SPEC, CEILING, SOCIAL, PROCESS, verification notes - why it works the way it does
outreach/      config and template (data files are gitignored)
examples/      synthetic worklists with the real column contracts
CHANGELOG.md   what changed and why, kept as the running record
```

# Social-channels motion - method

_Last updated 2026-06-24. The discourse counterpart to PROCESS.md (the repo stream). Finds graduates + in-market people from public social posts. MVP._

## What this is - and what it is NOT (read first)

Independent review reframed this honestly: **this is primarily a timing + wedge-language intelligence feed, plus a small stream of genuinely outreachable leads - not a 60-name cold-DM list.** Two reasons:
- **Most volume is pseudonymous.** Reddit dominates raw signal, but a Reddit handle has no off-platform identity - the only contact path is a Reddit reply/DM, which on r/lovable, r/SaaS, r/nocode reads as vendor self-promo and carries brand risk for a named founder.
- **The outreachable leads are the low-volume channels.** Hacker News, Mastodon, Bluesky, and Dev.to handles resolve to real people/sites; Reddit users only resolve when they name their product/company in the post.

So the deliverable is split: `social_contactable.csv` (warm AND identity-resolvable = the outreach shortlist) vs `social_leads.csv` (all warm, incl. pseudonymous Reddit = timing intelligence + optional in-thread reply). The verbatim `social_wedge.md` is high-value regardless of lead actionability - it is the messaging input.

## Channels (tested for free access from Claude Code)

| Channel | Access | Yield | Notes |
|---|---|---|---|
| **Reddit** | RSS `search.rss` (works; `.json` is 403) | volume spine | richest migration narratives, pseudonymous |
| **Hacker News** | Algolia API (free, no auth) | low vol, high identifiability | paginate; real handles |
| **Bluesky** | `api.bsky.app` searchPosts (free, no auth) | low vol, identifiable | **use precise queries** ("lovable.dev", "lovable supabase") - bare "lovable" is an English adjective, >90% noise. NOTE: `public.api.bsky.app` returns 403; use `api.bsky.app`. |
| **Mastodon** | public tag timelines (no auth) | low vol, identifiable | full-text `/v2/search` needs auth; tag timelines are open |
| **Dev.to** | Forem API (free) | ~0 warm | tag feed is vendor/SEO content; kept as a 1-call cost |
| X/Twitter, LinkedIn | login-walled, no free search | - | manual or paid only; documented gap |
| Discord | ToS / requires joining | - | excluded |

## Pipeline (mirrors the repo stream)
1. **Pull** - `pipeline/social_pull.py` queries all 5 channels, normalizes to one schema (source, author, author_url, date, title, text, url, query), dedups by url.
2. **Classify** - LLM over chunks: graduate_self_solver / in_market_complaint / quitter / rescue_seeker / happy_stayer / noise. Strict warm gate: `is_warm_lead` requires first-person the platform ownership AND a forward-looking exit/migration verb (rejects skeptic commentary, feature requests, pre-purchase questions, generic gripes). Adds `warm_score` 0-5 and `contactable`.
3. **Assemble** - `pipeline/social_assemble.py` author-dedups (one person = one lead, best item kept), writes the lead files + `social_wedge.md`, merges into the url-keyed store.
4. **Dedup store** - `store/social_seen.csv`, keyed by post url, with `first_seen`/`last_seen` and a `contacted` field so re-runs don't re-surface actioned people.

## Run
```bash
python3 pipeline/social_pull.py
# split data/social_raw_{date}.json into data/social_chunks/, run the classify workflow, then:
python3 pipeline/social_assemble.py <classify_output.json> {date} data/social_raw_{date}.json
```

## Motion guidance (founder-led, warm-first)
- **Outreach shortlist** = `social_contactable.csv`. Reach people where they have an off-platform identity (HN profile, Mastodon/Bluesky handle, personal site, or a named product). A reply that references their actual post, from the operator as a founder, not a pitch.
- **Reddit pseudonymous warm leads** = timing + wedge intelligence. Optional: a genuinely helpful in-thread reply (not a pitch) where it fits the subreddit norms. Do not mass-DM.
- **Wedge file** = the words the cohort uses ("Hotel California for your database", "just a preview window") - feed messaging, regardless of whether the author is reachable.
- Public data only; respect each platform's ToS and self-promo norms; no auto-DM; no scraping behind logins.

## Identity resolution (`pipeline/social_link.py`) - ATTEMPTED, modest yield

Goal: turn a pseudonymous social handle into a real identity (site/github/email) and cross-link to a repo graduate in the store. **Tested verdict: works for a small subset, NOT a reliable general bridge.**
- Result: **4 of 34** warm leads resolved to a real identity; **0** cross-linked to a repo in our store.
- WHY it's capped (all verified): warm posts almost never contain a GitHub URL (1/34) or a product domain (0/34) - people *describe* their migration, they don't link it. The repo store is a freshest-1k snapshot, so the odds a social poster's repo is in it are ~0. Reddit (the volume) is structurally unresolvable.
- Methods that DO work reliably when they fire (kept): `github-in-post`; `handle==github-user WITH a platform-origin repo` (collision-safe via corroboration - e.g. handleone); HN `about` field site (handletwo -> sitemap-example.one); Bluesky/Mastodon handle that is itself a domain (handlethree.dev).
- Tested and REJECTED: Reddit user-history RSS scan - rate-limited (HTTP 429) and the matches are RSS-boilerplate noise, not real links.
- Output: `leads/social_linked.csv`. This is a thin enrichment layer that will accumulate more as the social store grows; it does NOT unify the two streams. Reinforces the reframe: repo stream = resolvable identities (email); social stream = timing + wedge language + a handful of resolvable handles.

## Other gaps / next adds
- Bluesky via app-password session would unlock the authenticated `searchPosts` (more results) - not needed for MVP.
- A `contacted`-aware worklist view that hides actioned people on daily re-runs.

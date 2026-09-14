# Relicense migration: private archive + fresh public repo

Status: **EXECUTED 2026-09-14 (see the record at the end).** The plan below is
kept as drafted; the execution record lists what differed.

## Goal

Stop the MIT-era tree (`main` at `f9c341f` and every `refs/pull/*/head`) from
being publicly fetchable, while the live bots keep running and the public
identity of the project (name, URLs, plugin marketplace, Pages site) survives.

## Strategy (the two decisions that shape everything)

1. **Keep the name.** Rename the existing repo to
   `edisonymy/forecast-scaffold-mit-archive` and make it private, then create a
   NEW public `edisonymy/forecast-scaffold`. GitHub's rename redirect stops the
   moment a new repo takes the old name, which is exactly what we want. Every
   external pointer keeps working unchanged: both Cloud Scheduler kickers
   (`.../repos/edisonymy/forecast-scaffold/actions/workflows/*.yml/dispatches`),
   the forecast-exchange clone URL (Cloud Run entrypoint, CI, sync script), the
   plugin marketplace pointer, the Pages URL, `docs/journal.html`'s raw URL,
   and the seven user-agent strings.
2. **Single root commit.** The new repo starts from ONE squashed commit of the
   relicensed tree. If the old history rode along, `git checkout f9c341f` would
   hand anyone the MIT tree and defeat the purpose. Full history lives on in the
   private archive. Cost: no `git blame` past the root, no history links in
   CHANGELOG/HANDOVER (they already cite SHAs, which will 404 publicly; that is
   acceptable, the archive keeps them).

## Inventory (verified 2026-09-14)

| Thing | State | Action |
|---|---|---|
| Repo | public, 0 forks / 0 stars / 0 watchers, no branch protection, no rulesets, no webhooks, no deploy keys | rename + private |
| PR #47 (relicense, `f87b70b`) | draft, clean, 8 checks green | merge FIRST, it defines the snapshot |
| PR #45 (bench leaderboard), PR #43 (manifold redaction) | open | see decision D |
| Secrets (6) | `ASKNEWS_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `LEAK_PATTERNS`, `MANIFOLD_API_KEY`, `METACULUS_TOKEN`, `OPENROUTER_API_KEY` | re-enter in new repo; ⚠ `LEAK_PATTERNS` value is LOST (HANDOVER 2026-09-10), must be composed fresh |
| Variable | `TOURNAMENT_ID = fall-futureeval-2026,minibench` | re-create |
| Workflows (8) | `bot.yml` (dispatch, kicked every 10 min), `manifold.yml` (dispatch, kicker PAUSED), `journal-alarm.yml` (cron hourly), `resolution-sync.yml` (cron 6-hourly), `ci.yml`, `bench.yml`, `bot-test.yml`, `list-forecasts.yml` (dispatch only) | disable ALL in the archive after the cut, or they keep running there against the archive's secrets |
| Cloud Scheduler (project `edison-util-mcp`, us-central1) | `forecast-bot-kicker` `*/10 * * * *` ENABLED; `manifold-bot-kicker` `17 * * * *` PAUSED | pause the bot kicker for the cutover; leave manifold paused |
| ⚠ Kicker PAT | Bearer token in both jobs, type unknown | if fine-grained and scoped to the old repo by id, it will 403 on the new repo. Test with one manual dispatch after the new repo exists |
| GitHub Pages | live at `edisonymy.github.io/forecast-scaffold`, source `main:/docs`, `github-pages` environment | Pages goes down on a private repo; re-enable on the new repo (same URL) |
| Issues | 16 open (44, 39, 38, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1 + others), closed ones stay | see decision C |
| Tags / releases | `v0.1.0` release only; no `v0.4.28` tag yet | tag `v0.4.28` at `f9c341f` in the archive; `v0.5.0` on the new root |
| Live journal | `bot/journal/*.jsonl` (forecasts 3.0 MB, manifold 12 MB, resolutions 0.4 MB, traces 2.9 MB), appended by `bot.yml` / `manifold.yml` / `resolution-sync.yml` commits to `main` | snapshot only while every writer is stopped; verify tree equality |
| forecast-exchange (private) | `deploy/exchange-tick/entrypoint.sh` clones the public URL fresh per run (fine); local `scaffold/` is a clone that `scripts/sync_scaffold.sh` pulls `--ff-only` (will FAIL on unrelated history) | delete local `scaffold/` and re-clone after the cut |
| Local checkouts | main clone at `Documents/code/forecast-scaffold` + 12 worktrees (`ab/research-v2`, `ci-fix-2`, 6× `codex/*`, `fix/second-eyes-review`, one detached) with 18 remote branches | old clone keeps working against the archive if `origin` is repointed; fresh clone for the new repo |
| `edison-utility-function` | holds a vendored copy of the forecast skill only | no action |
| Software Heritage | ⚠ could not check (API behind a bot wall) | Edison: search `archive.softwareheritage.org` for the repo URL; if archived, the MIT tree stays public and this move buys much less |

## Decisions Edison must make before execution

- **A. Name strategy**: keep the name via rename (recommended, above) vs. a new
  name (every pointer above then needs editing). Recommendation: keep.
- **B. History**: single root commit (recommended) vs. full history (defeats the
  purpose). Recommendation: single root.
- **C. Issues**: transfer all 16 open in ascending number order (they get new
  numbers 1..16, and any `#N` references in docs/journal break), or transfer only
  the three live ones (44, 39, 38) and leave the preregistration record in the
  archive. Recommendation: transfer all; the preregistrations are part of the
  public track record.
- **D. Open PRs 43 and 45**: merge into the archive before the snapshot (they
  ship in the root commit), or port afterwards as patches (`git format-patch
  main..branch` → `git am` in the new clone). Recommendation: merge #45 if its
  checks are green; port #43 as patches since it is still being worked.
- **E. Stale local branches**: which of the 11 `codex/*`, `ab/*`, `fix/*`
  worktrees still matter? Anything kept must be ported as patches, never pushed
  as a branch (a pushed branch carries the old history into the public repo).
- **F. `LEAK_PATTERNS`**: Edison composes the new deny-list locally
  (`python scripts/leak_patterns_tool.py --template > ../deny-list.txt`, edit
  OUTSIDE the repo, never in chat) before cutover. `bot.yml` refuses to publish
  without it (line 164). This is a hard prerequisite.
- **G. Window**: the bot kicker will be paused ~30–45 min. Pick a slot with no
  open MiniBench/FutureEval spot questions (weekend or 02:00–06:00 UTC on a
  weekday). Cost of the pause: at most a couple of spot questions.

## Prerequisites (all in hand before step 1)

- [ ] Values for the five surviving secrets (`METACULUS_TOKEN`, `ASKNEWS_API_KEY`,
      `OPENROUTER_API_KEY`, `MANIFOLD_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`; the
      last can be regenerated with `claude setup-token`). Stored in a local env
      file, sourced with the CRLF-safe loop, never printed.
- [ ] Fresh `LEAK_PATTERNS` composed (decision F).
- [ ] Software Heritage checked (inventory row).
- [ ] Decisions A–G recorded here.

## Execution sequence

Every step is verified before the next. Bots are only stopped between steps 2
and 10.

1. **Merge the relicense.** Mark PR #47 ready, merge (merge commit, not
   squash, so `f87b70b` stays citable in the archive). Tag
   `v0.4.28` at `f9c341f` (`-m "Last release under the MIT License."`) and push
   the tag. Optionally merge #45 (decision D). CI green on `main`.
2. **Stop the writers.** `gcloud scheduler jobs pause forecast-bot-kicker
   --location us-central1`. Confirm `manifold-bot-kicker` is still paused.
   `gh workflow disable` for `journal-alarm.yml` and `resolution-sync.yml`.
   Wait until `gh run list --status in_progress --status queued` is empty.
   Record `main`'s SHA as `CUT_SHA`.
3. **Snapshot.** Fresh clone of `main` at `CUT_SHA` into a new directory.
   `git diff --stat CUT_SHA` against the local main clone must be empty.
4. **Rename + privatise the old repo.** `gh repo rename forecast-scaffold-mit-archive`
   then `gh repo edit --visibility private --accept-visibility-change-consequences`.
   Then in the archive: `gh workflow disable` for ALL 8 workflows, and delete the
   six secrets (`gh secret delete`) so nothing can run there by accident. Verify
   `https://github.com/edisonymy/forecast-scaffold` now 404s anonymously.
5. **Create the new public repo** `edisonymy/forecast-scaffold` (empty, no
   README, same description). Confirm the rename redirect is gone (the URL now
   resolves to the empty repo).
6. **Build the root commit.** In the snapshot directory: `rm -rf .git`, `git
   init -b main`, add everything, one commit: "forecast-scaffold 0.5.0 under
   PolyForm Noncommercial 1.0.0. Development history through v0.4.28 (MIT) is
   retained in a private archive." Push `main`. Tag `v0.5.0` on it and push.
7. **Configure the new repo.** Six secrets + `TOURNAMENT_ID` variable. Enable
   Pages (`main`, `/docs`). Verify `ci.yml` goes green on the root commit.
8. **Kicker test.** `gh workflow run bot.yml` manually first (proves secrets +
   `LEAK_PATTERNS`). Then `gcloud scheduler jobs run forecast-bot-kicker` once and
   confirm a run appears in the NEW repo. If it 403s, the PAT is repo-scoped:
   re-issue it with access to the new repo and update both scheduler jobs'
   Authorization header.
9. **Issues + PRs.** Transfer open issues (GraphQL `transferIssue`) in ascending
   order per decision C. Port kept branches as patches per decisions D/E.
10. **Resume.** `gcloud scheduler jobs resume forecast-bot-kicker`. `gh workflow
    enable` `journal-alarm.yml` and `resolution-sync.yml`. Watch the next two
    bot ticks complete and commit journal lines to the new `main`.
11. **Downstream.** forecast-exchange: delete local `scaffold/`, re-run
    `scripts/sync_scaffold.sh`; trigger one `exchange-tick` run and confirm the
    clone succeeds. Plugin: `/plugin marketplace update forecast-scaffold` then
    `/plugin update forecast-scaffold@forecast-scaffold` should show 0.5.0.
    Pages: `journal.html` loads the live journal.
12. **Local machine.** Move the old clone to `forecast-scaffold-mit-archive`
    and repoint `origin` (`git remote set-url origin
    https://github.com/edisonymy/forecast-scaffold-mit-archive.git`); its 12
    worktrees keep working. Fresh clone of the new repo at
    `Documents/code/forecast-scaffold`. Update `docs/HANDOVER.md` and the memory
    notes that describe the repo layout.

## Rollback

Until step 4 nothing is destructive. After step 4 and before step 6: rename the
archive back, make it public, re-enable the workflows, resume the kicker; the
only loss is the deleted secrets, which are re-entered from the env file. After
step 6: the new repo is authoritative; to roll back, delete it, rename the
archive back, and re-enable as above (journal lines committed to the new repo
in the meantime must be cherry-picked as patches).

## Verification checklist (done means all ticked)

- [ ] anonymous `git ls-remote https://github.com/edisonymy/forecast-scaffold`
      shows only `main`, `v0.5.0`; no `refs/pull/*` with MIT-era trees
- [ ] `git rev-list --count main` in the new repo is 1 plus post-cut commits
- [ ] two consecutive `bot.yml` runs green in the new repo with journal commits
- [ ] `exchange-tick` run green after the cut
- [ ] Pages URL serves `journal.html` with live data
- [ ] archive repo private, all workflows disabled, no secrets
- [ ] `v0.4.28` tag exists in the archive at `f9c341f`

## Execution record (2026-09-14, 21:40–22:05 UTC)

Decisions taken: A keep the name; B single root commit; C transfer all open
issues; D leave PRs 43 and 45 (both drafts) in the archive; E every local-only
branch and the detached worktree commit were pushed to the archive under
`archive/*` before the cut, nothing ported; F a fresh three-branch deny-list
was composed, verified clean against every tracked file, and stored OUTSIDE
the repo in `~/.forecast-scaffold.env` (`LEAK_PATTERNS=...`) so it is never
lost again; G Sunday 21:40 UTC, no spot questions open. Software Heritage:
Edison confirmed the repo was never archived.

What happened, in order:

1. PR #47 merged as `7204ea9` (merge commit). Tag `v0.4.28` → `f9c341f` pushed.
2. `forecast-bot-kicker` paused; `journal-alarm.yml` and `resolution-sync.yml`
   disabled; no runs in flight. `CUT_SHA = 7204ea9`.
3. Snapshot cloned; after `rm -rf .git` and re-add, `git write-tree` equalled
   `7204ea9^{tree}` (`efe3033b`), so the root commit is byte-identical.
4. Old repo renamed to `edisonymy/forecast-scaffold-mit-archive`; new public
   `edisonymy/forecast-scaffold` created under the original name.
5. Root commit `988c9f8` pushed as `main`; tag `v0.5.0` on it. CI green.
6. New repo: `TOURNAMENT_ID` variable set; `LEAK_PATTERNS` installed via
   `leak_patterns_tool.py --candidate --set`; Pages enabled (`main:/docs`,
   built); `resolution-sync.yml` left DISABLED until its secret exists.
7. 13 open issues transferred in ascending order (the handoff's "16" was
   stale): old #1–#10 kept their numbers; #38 → #11, #39 → #12, #44 → #13.
8. Archive: all 8 workflows disabled, then made private. Its six secrets were
   deliberately LEFT IN PLACE (see the open item below).
9. Kicker token verified: one manual `forecast-bot-kicker` fire produced a
   `workflow_dispatch` run in the NEW repo, which exited green with
   "METACULUS_TOKEN is not set — refusing a live run". Kicker resumed: it
   will keep no-op'ing every 10 min until the secrets exist, then start
   forecasting with no further action.
10. forecast-exchange: local `scaffold/` re-cloned at `988c9f8`; the Cloud Run
    job clones by URL and needs nothing.
11. Local: the old clone and every worktree now have `origin` →
    `forecast-scaffold-mit-archive`; the new clone is at
    `Documents/code/forecast-scaffold-new` (directory swap left to Edison,
    since this session was running inside the old tree).

### OPEN ITEM — the five API secrets (blocks the bot)

The machine-to-machine copy (a one-off workflow in the archive piping each
secret into `gh secret set` on the new repo) was blocked by the session's
permission classifier, so Edison enters them himself. Until then the bot
refuses live runs (green, harmless) and `resolution-sync.yml` stays disabled.

```
gh secret set METACULUS_TOKEN          --repo edisonymy/forecast-scaffold
gh secret set ASKNEWS_API_KEY          --repo edisonymy/forecast-scaffold
gh secret set OPENROUTER_API_KEY       --repo edisonymy/forecast-scaffold
gh secret set CLAUDE_CODE_OAUTH_TOKEN  --repo edisonymy/forecast-scaffold   # or: claude setup-token
gh secret set MANIFOLD_API_KEY         --repo edisonymy/forecast-scaffold   # only if the Manifold bot returns
gh workflow enable resolution-sync.yml --repo edisonymy/forecast-scaffold
```

Then, once two consecutive bot runs are green WITH journal commits, delete
the archive's secrets (`gh secret delete <NAME> --repo
edisonymy/forecast-scaffold-mit-archive`) so nothing can ever run there.

# parent-progress-sync

Keeps Linear parent issues sorted by how much of their work is done.

The parent-progress view sorts issues alphabetically by title, so this sync
writes a zero-padded percentage prefix onto every parent issue:

```
[100%] Migrate billing to Stripe
[067%] Ship SSO
[007%] Rewrite the importer
[000%] Q4 polish
```

Sorting those titles in descending order is the same as sorting by progress.

## How progress is calculated

`completed sub-issues / countable sub-issues`, where canceled sub-issues are
excluded from both sides. Rounding never overstates a milestone: a parent with
outstanding work never shows `100%`, and a parent with at least one completed
sub-issue never shows `000%`. Parents whose sub-issues were all canceled have
their prefix removed rather than being pinned to `000%`.

The prefix is rewritten in place, so runs are idempotent and a title is only
sent to Linear when its percentage actually changed.

## Configuration

Copy `.env.example` and fill in the credentials — everything is read from the
environment, nothing is stored in the repo.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LINEAR_API_KEY` | yes | — | Personal API key or OAuth token |
| `LINEAR_TEAM_KEY` | no | all teams | Restrict the sync to one team |
| `LINEAR_API_URL` | no | `https://api.linear.app/graphql` | API endpoint |
| `LINEAR_PAGE_SIZE` | no | `50` | Nodes requested per page (1–250) |
| `LINEAR_MAX_RETRIES` | no | `5` | Retries for rate-limited/transient failures |
| `LINEAR_DRY_RUN` | no | `false` | Report changes without writing |

## Usage

```bash
python -m parent_progress_sync --dry-run          # preview every change
python -m parent_progress_sync                    # apply
python -m parent_progress_sync --team ENG -v      # one team, debug logging
```

Always start with `--dry-run` against a new workspace — the sync rewrites issue
titles.

`.github/workflows/sync.yml` runs it hourly and can be triggered manually with
a dry-run toggle.

## API handling

Requests are paginated by cursor, and HTTP `429`/`5xx` responses and GraphQL
`RATELIMITED` errors are retried with exponential backoff, honouring
`Retry-After` when Linear supplies it. Parents with more sub-issues than fit in
the embedded page are fetched with a follow-up paginated query.

## Tests

```bash
python -m unittest discover -s tests -t .
```

No third-party dependencies — the client is built on `urllib`.

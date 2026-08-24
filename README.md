# parent-progress-sync

Groups Linear parent issues by how much of their work is done.

Linear list views [cannot be ordered by title](https://linear.app/docs/display-options)
— ordering offers Status, Manual, Priority, Last created, Last updated, Due
date, and Link count. They *can* be grouped by label, so this sync writes
progress as a label inside one managed group:

```
Parent progress
├── 000% not started
├── 001-024%
├── 025-049%
├── 050-074%
├── 075-099%
└── 100% complete
```

Group your view by label (`Shift V` → Grouping → Label) and the sections appear
in progress order, because the zero-padded numeric names sort that way.

Exactly one bucket applies to a parent at a time, and **labels outside the
managed group are never touched** — the sync uses add/remove mutations rather
than replacing an issue's label set, and only ever removes labels that live
inside its own group.

## How progress is calculated

`completed sub-issues / countable sub-issues`, where canceled sub-issues are
excluded from both sides. Rounding never overstates a milestone: a parent with
outstanding work never lands in `100% complete`, and a parent with at least one
completed sub-issue never lands in `000% not started`. Parents whose sub-issues
were **all** canceled get no bucket at all — any existing one is removed —
since there is no meaningful progress to report.

Runs are idempotent: a parent already in the right bucket produces no writes.

## First run

The sync will not create labels behind your back. On a workspace without the
group it exits with an error telling you to opt in:

```bash
python -m parent_progress_sync --dry-run --bootstrap-labels   # preview
python -m parent_progress_sync --bootstrap-labels             # create + apply
```

Bootstrap creates the group and any missing buckets as **workspace** labels
(not team-scoped), then continues. Later runs need no flag.

Before creating anything it validates that the setup is unambiguous, and
refuses to run if a bucket name is already taken outside the group, if two
labels share the group's name, or if the group name belongs to a plain label.
Fix the collision rather than letting the sync guess which label you meant.

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
| `LINEAR_LABEL_GROUP` | no | `Parent progress` | Name of the managed label group |
| `LINEAR_BOOTSTRAP_LABELS` | no | `false` | Create the group and missing buckets |
| `LINEAR_CLEANUP_LEGACY_PREFIXES` | no | `false` | Strip `[042%] ` title prefixes |

## Usage

```bash
python -m parent_progress_sync --dry-run          # preview every change
python -m parent_progress_sync                    # apply
python -m parent_progress_sync --team ENG         # one team only
python -m parent_progress_sync -v                 # debug logging (prints titles)
```

Always start with `--dry-run` against a new workspace.

### Migrating from the title-prefix version

An earlier version wrote `[042%] ` prefixes into issue titles. That approach
never worked — no Linear view can sort by title — and it has been removed.
`--cleanup-legacy-prefixes` strips any prefixes it left behind, as part of a
normal run:

```bash
python -m parent_progress_sync --dry-run --cleanup-legacy-prefixes
python -m parent_progress_sync --cleanup-legacy-prefixes
```

It only strips the exact `[NNN%] ` shape that version wrote, leaving titles
like `[WIP] Ship login` alone. Off by default, since it renames issues.

`.github/workflows/sync.yml` runs it hourly and can be triggered manually with
a dry-run toggle.

## Privacy and credentials

This tool reads your issue tracker, so be deliberate about where it runs and
where its output goes.

**Logs never contain issue titles by default.** At the default level the sync
reports only identifiers and percentages:

```
INFO Updating ENG-42: -025-049%; +050-074%
```

Full before/after titles appear only under `--verbose`. Don't pass `--verbose`
in a scheduled job whose logs are readable by a wider audience than your Linear
workspace — **GitHub Actions logs on a public repository are visible to
anyone**, and are retained for 90 days by default. Error text from the API is
truncated for the same reason, since it can echo the request back into the log.

**The API key needs write access** to label issues. Store it as an Actions
secret, never in the repo. Anyone who can push a workflow to the repository can
use that secret to read and modify your Linear issues, so on a public repo keep
`Settings -> Actions -> Fork pull request workflows` at its restrictive default
and treat push access as equivalent to Linear write access.

Scope the blast radius with `LINEAR_TEAM_KEY` if only one team needs syncing.

## API handling

Requests are paginated by cursor, and HTTP `429`/`5xx` responses and GraphQL
`RATELIMITED` errors are retried with exponential backoff, honouring
`Retry-After` when Linear supplies it. Parents with more sub-issues or labels
than fit in their embedded page are fetched with a follow-up paginated query,
as is label discovery.

Within a parent, the stale bucket is removed before the new one is added, so an
issue is never briefly in two buckets at once.

## Tests

```bash
python -m unittest discover -s tests -t .
```

No third-party dependencies — the client is built on `urllib`.

## License

MIT — see [LICENSE](LICENSE).

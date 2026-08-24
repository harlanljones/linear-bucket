"""Scheduled sync that keeps parent-issue title prefixes in step with progress."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

from .config import Config
from .linear_client import LinearClient
from .progress import Progress, apply_prefix, parse_prefix, strip_prefix

logger = logging.getLogger(__name__)

#: Parent issues, i.e. issues that have at least one sub-issue.
PARENT_ISSUES_QUERY = """
query ParentIssues($first: Int!, $after: String, $filter: IssueFilter) {
  issues(first: $first, after: $after, filter: $filter) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      children(first: 250) {
        pageInfo { hasNextPage }
        nodes { id state { type } }
      }
    }
  }
}
"""

#: Fallback for parents whose children did not fit in the embedded page.
CHILDREN_QUERY = """
query IssueChildren($id: String!, $first: Int!, $after: String) {
  issue(id: $id) {
    children(first: $first, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id state { type } }
    }
  }
}
"""

UPDATE_TITLE_MUTATION = """
mutation UpdateIssueTitle($id: String!, $title: String!) {
  issueUpdate(id: $id, input: { title: $title }) {
    success
  }
}
"""

#: Workflow state types Linear considers finished.
COMPLETED_STATE_TYPES = frozenset({"completed"})

#: Sub-issues in these states do not count toward the denominator.
IGNORED_STATE_TYPES = frozenset({"canceled"})


@dataclass(frozen=True)
class TitleUpdate:
    issue_id: str
    identifier: str
    old_title: str
    new_title: str
    progress: Progress
    old_percent: int | None
    new_percent: int | None

    def describe(self) -> str:
        """Summarise the change without revealing the issue title."""
        return f"{self.identifier}: {_percent_label(self.old_percent)} -> {_percent_label(self.new_percent)}"


def _percent_label(percent: int | None) -> str:
    return "no prefix" if percent is None else f"{percent}%"


@dataclass
class SyncReport:
    parents_scanned: int = 0
    parents_skipped: int = 0
    updates: list[TitleUpdate] = field(default_factory=list)
    dry_run: bool = False

    @property
    def parents_updated(self) -> int:
        return len(self.updates)

    def summary(self) -> str:
        verb = "would update" if self.dry_run else "updated"
        return (
            f"Scanned {self.parents_scanned} parent issue(s): "
            f"{verb} {self.parents_updated}, left {self.parents_skipped} unchanged"
        )


def count_progress(children: Iterable[Mapping[str, Any]]) -> Progress:
    """Count completed and countable sub-issues, ignoring canceled ones."""
    completed = 0
    total = 0
    for child in children:
        state_type = ((child.get("state") or {}).get("type") or "").lower()
        if state_type in IGNORED_STATE_TYPES:
            continue
        total += 1
        if state_type in COMPLETED_STATE_TYPES:
            completed += 1
    return Progress(completed=completed, total=total)


class ProgressSync:
    def __init__(self, client: LinearClient, config: Config) -> None:
        self._client = client
        self._config = config

    def run(self) -> SyncReport:
        report = SyncReport(dry_run=self._config.dry_run)

        for parent in self._iter_parents():
            report.parents_scanned += 1
            update = self._plan_update(parent)
            if update is None:
                report.parents_skipped += 1
                continue

            verb = "Would update" if self._config.dry_run else "Updating"
            # Issue titles are omitted at INFO: scheduled runs commonly log to
            # CI, where the output is readable by anyone with repo access.
            logger.info("%s %s", verb, update.describe())
            logger.debug("%s %s: %r -> %r", verb, update.identifier, update.old_title, update.new_title)
            if not self._config.dry_run:
                self._update_title(update)
            report.updates.append(update)

        logger.info("%s", report.summary())
        return report

    def _iter_parents(self) -> Iterator[Mapping[str, Any]]:
        issue_filter: dict[str, Any] = {"children": {"some": {}}}
        if self._config.team_key:
            issue_filter["team"] = {"key": {"eq": self._config.team_key}}
        return self._client.paginate(
            PARENT_ISSUES_QUERY,
            {"filter": issue_filter},
            ("issues",),
            page_size=self._config.page_size,
        )

    def _plan_update(self, parent: Mapping[str, Any]) -> TitleUpdate | None:
        children = self._children_of(parent)
        progress = count_progress(children)
        title = parent.get("title") or ""
        old_percent = parse_prefix(title)

        if progress.total == 0:
            # Every sub-issue was canceled; drop a stale prefix rather than
            # claiming 0% progress on work that no longer exists.
            new_percent = None
            new_title = strip_prefix(title).strip()
            if new_title == title:
                return None
        else:
            new_percent = progress.percent
            if old_percent == new_percent:
                return None
            new_title = apply_prefix(title, new_percent)
            if new_title == title:
                return None

        return TitleUpdate(
            issue_id=parent["id"],
            identifier=parent.get("identifier", parent["id"]),
            old_title=title,
            new_title=new_title,
            progress=progress,
            old_percent=old_percent,
            new_percent=new_percent,
        )

    def _children_of(self, parent: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        connection = parent.get("children") or {}
        nodes = list(connection.get("nodes") or [])
        if not (connection.get("pageInfo") or {}).get("hasNextPage"):
            return nodes

        # Rare: a parent with more sub-issues than the embedded page holds.
        return list(
            self._client.paginate(
                CHILDREN_QUERY,
                {"id": parent["id"]},
                ("issue", "children"),
                page_size=self._config.page_size,
            )
        )

    def _update_title(self, update: TitleUpdate) -> None:
        data = self._client.execute(
            UPDATE_TITLE_MUTATION,
            {"id": update.issue_id, "title": update.new_title},
        )
        if not ((data.get("issueUpdate") or {}).get("success")):
            raise RuntimeError(f"Linear rejected the title update for {update.identifier}")

"""Scheduled sync that keeps a parent-issue progress label in step with progress.

Linear list views can be grouped by label but not ordered by title, so progress
is written as one label from a managed group. Labels outside that group are
never touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

from .buckets import bucket_for
from .config import Config
from .labels import LabelCatalog, ManagedLabels
from .linear_client import LinearClient
from .progress import Progress, parse_prefix, strip_prefix

logger = logging.getLogger(__name__)

PARENT_ISSUES_QUERY = """
query ParentIssues($first: Int!, $after: String, $filter: IssueFilter) {
  issues(first: $first, after: $after, filter: $filter) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      labels(first: 100) {
        pageInfo { hasNextPage }
        nodes { id }
      }
      children(first: 250) {
        pageInfo { hasNextPage }
        nodes { id state { type } }
      }
    }
  }
}
"""

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

ISSUE_LABELS_QUERY = """
query IssueLabels($id: String!, $first: Int!, $after: String) {
  issue(id: $id) {
    labels(first: $first, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id }
    }
  }
}
"""

ADD_LABEL_MUTATION = """
mutation AddIssueLabel($id: String!, $labelId: String!) {
  issueAddLabel(id: $id, labelId: $labelId) { success }
}
"""

REMOVE_LABEL_MUTATION = """
mutation RemoveIssueLabel($id: String!, $labelId: String!) {
  issueRemoveLabel(id: $id, labelId: $labelId) { success }
}
"""

UPDATE_TITLE_MUTATION = """
mutation UpdateIssueTitle($id: String!, $title: String!) {
  issueUpdate(id: $id, input: { title: $title }) { success }
}
"""

#: Workflow state types Linear considers finished.
COMPLETED_STATE_TYPES = frozenset({"completed"})

#: Sub-issues in these states do not count toward the denominator.
IGNORED_STATE_TYPES = frozenset({"canceled"})


@dataclass(frozen=True)
class IssueChange:
    """The label (and optional legacy title) edits planned for one parent."""

    issue_id: str
    identifier: str
    progress: Progress
    add_label: str | None = None
    add_label_name: str | None = None
    remove_labels: tuple[str, ...] = ()
    remove_label_names: tuple[str, ...] = ()
    old_title: str | None = None
    new_title: str | None = None

    @property
    def renames_title(self) -> bool:
        return self.new_title is not None

    def describe(self) -> str:
        """Summarise the change without revealing the issue title."""
        parts = []
        if self.remove_label_names:
            parts.append(f"-{', -'.join(self.remove_label_names)}")
        if self.add_label_name:
            parts.append(f"+{self.add_label_name}")
        if self.renames_title:
            parts.append("legacy prefix removed")
        return f"{self.identifier}: {'; '.join(parts) or 'no change'}"


@dataclass
class SyncReport:
    parents_scanned: int = 0
    parents_skipped: int = 0
    changes: list[IssueChange] = field(default_factory=list)
    dry_run: bool = False

    @property
    def parents_updated(self) -> int:
        return len(self.changes)

    @property
    def titles_cleaned(self) -> int:
        return sum(1 for change in self.changes if change.renames_title)

    def summary(self) -> str:
        verb = "would update" if self.dry_run else "updated"
        summary = (
            f"Scanned {self.parents_scanned} parent issue(s): "
            f"{verb} {self.parents_updated}, left {self.parents_skipped} unchanged"
        )
        if self.titles_cleaned:
            summary += f" ({self.titles_cleaned} legacy prefix(es) removed)"
        return summary


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
        labels = LabelCatalog(
            self._client, self._config.label_group, self._config.page_size
        ).resolve(bootstrap=self._config.bootstrap_labels, dry_run=self._config.dry_run)

        report = SyncReport(dry_run=self._config.dry_run)
        for parent in self._iter_parents():
            report.parents_scanned += 1
            change = self._plan_change(parent, labels)
            if change is None:
                report.parents_skipped += 1
                continue

            verb = "Would update" if self._config.dry_run else "Updating"
            # Issue titles are omitted at INFO: scheduled runs commonly log to
            # CI, where the output is readable by anyone with repo access.
            logger.info("%s %s", verb, change.describe())
            if change.renames_title:
                logger.debug("%s %s: %r -> %r", verb, change.identifier, change.old_title, change.new_title)

            if not self._config.dry_run:
                self._apply(change)
            report.changes.append(change)

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

    def _plan_change(
        self, parent: Mapping[str, Any], labels: ManagedLabels
    ) -> IssueChange | None:
        progress = count_progress(self._children_of(parent))

        # Every sub-issue was canceled: there is no meaningful progress to
        # report, so the parent carries no bucket at all.
        target_id: str | None = None
        target_name: str | None = None
        if progress.total > 0:
            target_name = bucket_for(progress.percent).name
            target_id = labels.id_for(target_name)

        applied = self._label_ids_of(parent)
        managed_applied = applied & labels.managed_ids
        stale = tuple(sorted(managed_applied - {target_id} if target_id else managed_applied))
        needs_add = target_id is not None and target_id not in managed_applied

        title = parent.get("title") or ""
        old_title = new_title = None
        if self._config.cleanup_legacy_prefixes and parse_prefix(title) is not None:
            stripped = strip_prefix(title).strip()
            if stripped and stripped != title:
                old_title, new_title = title, stripped

        if not needs_add and not stale and new_title is None:
            return None

        return IssueChange(
            issue_id=parent["id"],
            identifier=parent.get("identifier", parent["id"]),
            progress=progress,
            add_label=target_id if needs_add else None,
            add_label_name=target_name if needs_add else None,
            remove_labels=stale,
            remove_label_names=tuple(labels.name_for(label_id) for label_id in stale),
            old_title=old_title,
            new_title=new_title,
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

    def _label_ids_of(self, parent: Mapping[str, Any]) -> set[str]:
        connection = parent.get("labels") or {}
        if not (connection.get("pageInfo") or {}).get("hasNextPage"):
            return {str(label["id"]) for label in connection.get("nodes") or []}

        return {
            str(label["id"])
            for label in self._client.paginate(
                ISSUE_LABELS_QUERY,
                {"id": parent["id"]},
                ("issue", "labels"),
                page_size=self._config.page_size,
            )
        }

    def _apply(self, change: IssueChange) -> None:
        # Remove first so an issue is never briefly in two buckets at once.
        for label_id in change.remove_labels:
            self._mutate(
                REMOVE_LABEL_MUTATION,
                {"id": change.issue_id, "labelId": label_id},
                "issueRemoveLabel",
                change.identifier,
            )
        if change.add_label:
            self._mutate(
                ADD_LABEL_MUTATION,
                {"id": change.issue_id, "labelId": change.add_label},
                "issueAddLabel",
                change.identifier,
            )
        if change.new_title is not None:
            self._mutate(
                UPDATE_TITLE_MUTATION,
                {"id": change.issue_id, "title": change.new_title},
                "issueUpdate",
                change.identifier,
            )

    def _mutate(
        self, mutation: str, variables: Mapping[str, Any], field_name: str, identifier: str
    ) -> None:
        data = self._client.execute(mutation, variables)
        if not ((data.get(field_name) or {}).get("success")):
            raise RuntimeError(f"Linear rejected {field_name} for {identifier}")

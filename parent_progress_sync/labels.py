"""Discovery, collision validation, and bootstrap of the managed label group.

The sync only ever adds or removes labels inside one group it owns. Everything
here exists to establish, safely, which label IDs those are — and to refuse to
run rather than guess when the workspace looks ambiguous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .buckets import BUCKET_NAMES
from .linear_client import LinearClient

logger = logging.getLogger(__name__)

LABELS_BY_NAME_QUERY = """
query LabelsByName($first: Int!, $after: String, $names: [String!]) {
  issueLabels(first: $first, after: $after, filter: { name: { in: $names } }) {
    pageInfo { hasNextPage endCursor }
    nodes { id name isGroup parent { id name } }
  }
}
"""

GROUP_CHILDREN_QUERY = """
query GroupChildren($first: Int!, $after: String, $groupId: ID!) {
  issueLabels(first: $first, after: $after, filter: { parent: { id: { eq: $groupId } } }) {
    pageInfo { hasNextPage endCursor }
    nodes { id name }
  }
}
"""

CREATE_LABEL_MUTATION = """
mutation CreateLabel($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) {
    success
    issueLabel { id name }
  }
}
"""

#: Placeholder ID used when a dry run reports a label it would have created.
UNCREATED_ID_PREFIX = "<would-create:"


class LabelError(RuntimeError):
    """The managed label group is missing or ambiguous."""


@dataclass(frozen=True)
class ManagedLabels:
    """Resolved IDs for the group the sync is allowed to touch."""

    group_id: str
    bucket_ids: Mapping[str, str]
    #: Every label inside the group, including ones this version doesn't know
    #: about, so renamed or retired buckets are still cleaned off issues.
    managed_ids: frozenset[str]

    def id_for(self, bucket_name: str) -> str:
        return self.bucket_ids[bucket_name]

    def name_for(self, label_id: str) -> str:
        for name, candidate in self.bucket_ids.items():
            if candidate == label_id:
                return name
        return "unknown bucket"


class LabelCatalog:
    def __init__(self, client: LinearClient, group_name: str, page_size: int = 50) -> None:
        self._client = client
        self._group_name = group_name
        self._page_size = page_size

    def resolve(self, *, bootstrap: bool, dry_run: bool) -> ManagedLabels:
        """Find the managed group, validating that it is unambiguous.

        Creates anything missing when ``bootstrap`` is set; a dry run reports
        the same plan without writing.
        """
        matches = list(self._fetch_by_name([self._group_name, *BUCKET_NAMES]))
        group_id = self._resolve_group(matches, bootstrap=bootstrap, dry_run=dry_run)
        self._reject_collisions(matches, group_id)

        children = {} if _is_placeholder(group_id) else self._fetch_children(group_id)
        bucket_ids = dict(self._resolve_buckets(children, group_id, bootstrap=bootstrap, dry_run=dry_run))

        return ManagedLabels(
            group_id=group_id,
            bucket_ids=bucket_ids,
            managed_ids=frozenset(children.values()) | frozenset(bucket_ids.values()),
        )

    def _resolve_group(
        self, matches: list[Mapping[str, Any]], *, bootstrap: bool, dry_run: bool
    ) -> str:
        named = [label for label in matches if label.get("name") == self._group_name]

        if len(named) > 1:
            raise LabelError(
                f"{len(named)} labels are named {self._group_name!r}; "
                "rename or merge them so the managed group is unambiguous"
            )
        if named:
            group = named[0]
            if not group.get("isGroup"):
                raise LabelError(
                    f"A label named {self._group_name!r} exists but is not a label group. "
                    "Rename it, or point the sync at a different group."
                )
            if group.get("parent"):
                raise LabelError(
                    f"The label group {self._group_name!r} is nested inside "
                    f"{group['parent'].get('name')!r}; the sync expects a top-level group."
                )
            return str(group["id"])

        if not bootstrap:
            raise LabelError(
                f"No label group named {self._group_name!r} exists, and label creation "
                "is disabled (--no-bootstrap-labels)."
            )
        return self._create_label(self._group_name, parent_id=None, dry_run=dry_run)

    def _reject_collisions(self, matches: Iterable[Mapping[str, Any]], group_id: str) -> None:
        """Refuse to run if a bucket name is already taken outside the group."""
        seen: dict[str, str] = {}
        for label in matches:
            name = label.get("name")
            if name not in BUCKET_NAMES:
                continue

            parent_id = (label.get("parent") or {}).get("id")
            if parent_id != group_id:
                where = f"under {label['parent']['name']!r}" if label.get("parent") else "at the top level"
                raise LabelError(
                    f"A label named {name!r} already exists {where}, outside the managed group "
                    f"{self._group_name!r}. Rename it so the sync cannot confuse the two."
                )
            if name in seen:
                raise LabelError(f"Duplicate label {name!r} inside {self._group_name!r}")
            seen[str(name)] = str(label["id"])

    def _resolve_buckets(
        self,
        children: Mapping[str, str],
        group_id: str,
        *,
        bootstrap: bool,
        dry_run: bool,
    ) -> Iterable[tuple[str, str]]:
        missing = [name for name in BUCKET_NAMES if name not in children]
        if missing and not bootstrap:
            raise LabelError(
                f"The label group {self._group_name!r} is missing {len(missing)} bucket(s): "
                f"{', '.join(missing)}, and label creation is disabled "
                "(--no-bootstrap-labels)."
            )

        for name in BUCKET_NAMES:
            if name in children:
                yield name, children[name]
            else:
                yield name, self._create_label(name, parent_id=group_id, dry_run=dry_run)

    def _create_label(self, name: str, *, parent_id: str | None, dry_run: bool) -> str:
        if dry_run:
            logger.info("Would create label %r", name)
            return f"{UNCREATED_ID_PREFIX}{name}>"

        payload: dict[str, Any] = {"name": name}
        if parent_id is None:
            payload["isGroup"] = True
        else:
            payload["parentId"] = parent_id

        data = self._client.execute(CREATE_LABEL_MUTATION, {"input": payload})
        result = data.get("issueLabelCreate") or {}
        if not result.get("success") or not (result.get("issueLabel") or {}).get("id"):
            raise LabelError(f"Linear rejected creation of label {name!r}")

        logger.info("Created label %r", name)
        return str(result["issueLabel"]["id"])

    def _fetch_by_name(self, names: list[str]) -> Iterable[Mapping[str, Any]]:
        return self._client.paginate(
            LABELS_BY_NAME_QUERY,
            {"names": names},
            ("issueLabels",),
            page_size=self._page_size,
        )

    def _fetch_children(self, group_id: str) -> dict[str, str]:
        children: dict[str, str] = {}
        for label in self._client.paginate(
            GROUP_CHILDREN_QUERY,
            {"groupId": group_id},
            ("issueLabels",),
            page_size=self._page_size,
        ):
            children[str(label["name"])] = str(label["id"])
        return children


def _is_placeholder(label_id: str) -> bool:
    return label_id.startswith(UNCREATED_ID_PREFIX)

import unittest

from parent_progress_sync.buckets import BUCKET_NAMES, DEFAULT_GROUP_NAME
from parent_progress_sync.config import Config
from parent_progress_sync.sync import ProgressSync, count_progress

GROUP_ID = "group-1"
UNRELATED_LABEL = "label-bug"


def label_id(bucket_name):
    return f"label-{bucket_name}"


def child(state_type):
    return {"id": f"child-{state_type}", "state": {"type": state_type}}


def children(completed=0, started=0, canceled=0):
    return (
        [child("completed") for _ in range(completed)]
        + [child("started") for _ in range(started)]
        + [child("canceled") for _ in range(canceled)]
    )


def applied(label_id_value, group=None):
    """A label as returned on an issue, optionally inside a label group."""
    return {"id": label_id_value, "parent": {"id": group} if group else None}


def parent(
    issue_id="p1",
    title="Ship login",
    kids=(),
    labels=(),
    kids_truncated=False,
    labels_truncated=False,
):
    nodes = [
        value if isinstance(value, dict) else applied(value, GROUP_ID)
        if value.startswith("label-0") or value.startswith("label-1")
        else applied(value)
        for value in labels
    ]
    return {
        "id": issue_id,
        "identifier": issue_id.upper(),
        "title": title,
        "labels": {"pageInfo": {"hasNextPage": labels_truncated}, "nodes": nodes},
        "children": {"pageInfo": {"hasNextPage": kids_truncated}, "nodes": list(kids)},
    }


class FakeClient:
    """Serves label discovery and issue pages, recording every mutation."""

    def __init__(self, parents, extra_children=(), extra_labels=()):
        self._parents = list(parents)
        self._extra_children = list(extra_children)
        self._extra_labels = list(extra_labels)
        self.mutations = []

    def paginate(self, query, variables, path, page_size=50):
        if "names" in variables:
            names = set(variables["names"])
            found = []
            if DEFAULT_GROUP_NAME in names:
                found.append(
                    {"id": GROUP_ID, "name": DEFAULT_GROUP_NAME, "isGroup": True, "parent": None}
                )
            found.extend(
                {
                    "id": label_id(name),
                    "name": name,
                    "isGroup": False,
                    "parent": {"id": GROUP_ID, "name": DEFAULT_GROUP_NAME},
                }
                for name in BUCKET_NAMES
                if name in names
            )
            return iter(found)
        if "groupId" in variables:
            return iter([{"id": label_id(name), "name": name} for name in BUCKET_NAMES])
        if tuple(path) == ("issues",):
            return iter(self._parents)
        if tuple(path) == ("issue", "children"):
            return iter(self._extra_children)
        return iter(self._extra_labels)

    def execute(self, query, variables=None):
        variables = dict(variables or {})
        for name in ("issueAddLabel", "issueRemoveLabel", "issueUpdate"):
            if name in query:
                self.mutations.append((name, variables))
                return {name: {"success": True}}
        raise AssertionError(f"unexpected mutation: {query}")

    def labels_added(self):
        return [v["labelId"] for name, v in self.mutations if name == "issueAddLabel"]

    def labels_removed(self):
        return [v["labelId"] for name, v in self.mutations if name == "issueRemoveLabel"]


def config(**overrides):
    return Config(api_key="k", **overrides)


def run(client, **overrides):
    return ProgressSync(client, config(**overrides)).run()


class CountProgressTests(unittest.TestCase):
    def test_counts_completed_children(self):
        progress = count_progress(children(completed=1, started=2))
        self.assertEqual((progress.completed, progress.total), (1, 3))

    def test_canceled_children_are_excluded(self):
        progress = count_progress(children(completed=1, canceled=1))
        self.assertEqual((progress.completed, progress.total), (1, 1))
        self.assertEqual(progress.percent, 100)

    def test_state_type_casing_is_ignored(self):
        self.assertEqual(count_progress([{"state": {"type": "Completed"}}]).completed, 1)

    def test_missing_state_counts_as_incomplete(self):
        self.assertEqual(count_progress([{"id": "x"}]).completed, 0)


class LabelAssignmentTests(unittest.TestCase):
    def test_adds_bucket_to_unlabelled_parent(self):
        client = FakeClient([parent(kids=children(completed=1, started=1))])

        report = run(client)

        self.assertEqual(report.parents_updated, 1)
        self.assertEqual(client.labels_added(), [label_id("050-074%")])
        self.assertEqual(client.labels_removed(), [])

    def test_not_started_parent_gets_the_zero_bucket(self):
        client = FakeClient([parent(kids=children(started=3))])

        run(client)

        self.assertEqual(client.labels_added(), [label_id("000% not started")])

    def test_fully_complete_parent(self):
        client = FakeClient([parent(kids=children(completed=2))])

        run(client)

        self.assertEqual(client.labels_added(), [label_id("100% complete")])

    def test_transition_replaces_the_previous_bucket(self):
        client = FakeClient(
            [parent(kids=children(completed=3, started=1), labels=[label_id("025-049%")])]
        )

        run(client)

        self.assertEqual(client.labels_removed(), [label_id("025-049%")])
        self.assertEqual(client.labels_added(), [label_id("075-099%")])

    def test_removal_precedes_addition(self):
        client = FakeClient(
            [parent(kids=children(completed=2), labels=[label_id("001-024%")])]
        )

        run(client)

        self.assertEqual([name for name, _ in client.mutations], ["issueRemoveLabel", "issueAddLabel"])

    def test_idempotent_when_already_correct(self):
        client = FakeClient(
            [parent(kids=children(completed=1, started=1), labels=[label_id("050-074%")])]
        )

        report = run(client)

        self.assertEqual(report.parents_skipped, 1)
        self.assertEqual(client.mutations, [])

    def test_unrelated_labels_are_preserved(self):
        client = FakeClient(
            [
                parent(
                    kids=children(completed=1),
                    labels=[UNRELATED_LABEL, "label-frontend", label_id("001-024%")],
                )
            ]
        )

        run(client)

        # Only the stale managed bucket is removed; nothing else is touched.
        self.assertEqual(client.labels_removed(), [label_id("001-024%")])
        self.assertNotIn(UNRELATED_LABEL, client.labels_removed())
        self.assertNotIn("label-frontend", client.labels_removed())

    def test_unrelated_labels_alone_need_no_mutation_beyond_the_add(self):
        client = FakeClient([parent(kids=children(completed=1), labels=[UNRELATED_LABEL])])

        run(client)

        self.assertEqual(client.labels_removed(), [])
        self.assertEqual(client.labels_added(), [label_id("100% complete")])

    def test_multiple_stale_buckets_are_all_removed(self):
        client = FakeClient(
            [
                parent(
                    kids=children(completed=1, started=1),
                    labels=[label_id("001-024%"), label_id("100% complete")],
                )
            ]
        )

        run(client)

        self.assertEqual(
            sorted(client.labels_removed()),
            sorted([label_id("001-024%"), label_id("100% complete")]),
        )
        self.assertEqual(client.labels_added(), [label_id("050-074%")])


class CancellationTests(unittest.TestCase):
    def test_all_canceled_parent_receives_no_bucket(self):
        client = FakeClient([parent(kids=children(canceled=2))])

        report = run(client)

        self.assertEqual(report.parents_skipped, 1)
        self.assertEqual(client.mutations, [])

    def test_all_canceled_parent_has_its_bucket_removed(self):
        client = FakeClient(
            [parent(kids=children(canceled=2), labels=[label_id("050-074%"), UNRELATED_LABEL])]
        )

        run(client)

        self.assertEqual(client.labels_removed(), [label_id("050-074%")])
        self.assertEqual(client.labels_added(), [])


class DryRunTests(unittest.TestCase):
    def test_dry_run_reports_without_writing(self):
        client = FakeClient(
            [parent(kids=children(completed=1, started=1), labels=[label_id("001-024%")])]
        )

        report = run(client, dry_run=True)

        self.assertEqual(report.parents_updated, 1)
        self.assertEqual(client.mutations, [])
        self.assertIn("would update 1", report.summary())

    def test_dry_run_describes_the_transition(self):
        client = FakeClient(
            [parent(kids=children(completed=1), labels=[label_id("001-024%")])]
        )

        report = run(client, dry_run=True)

        self.assertEqual(
            report.changes[0].describe(), "P1: -001-024%; +100% complete"
        )


class LegacyCleanupTests(unittest.TestCase):
    def test_prefix_left_alone_by_default(self):
        client = FakeClient([parent(title="[025%] Ship login", kids=children(completed=1))])

        run(client)

        self.assertEqual([name for name, _ in client.mutations], ["issueAddLabel"])

    def test_prefix_stripped_when_enabled(self):
        client = FakeClient([parent(title="[025%] Ship login", kids=children(completed=1))])

        run(client, cleanup_legacy_prefixes=True)

        self.assertIn(("issueUpdate", {"id": "p1", "title": "Ship login"}), client.mutations)

    def test_titles_without_a_prefix_are_untouched(self):
        client = FakeClient([parent(title="Ship login", kids=children(completed=1))])

        run(client, cleanup_legacy_prefixes=True)

        self.assertEqual([name for name, _ in client.mutations], ["issueAddLabel"])

    def test_cleanup_alone_counts_as_an_update(self):
        client = FakeClient(
            [
                parent(
                    title="[025%] Ship login",
                    kids=children(completed=1),
                    labels=[label_id("100% complete")],
                )
            ]
        )

        report = run(client, cleanup_legacy_prefixes=True)

        self.assertEqual(report.titles_cleaned, 1)
        self.assertIn("1 legacy prefix(es) removed", report.summary())


class PaginationTests(unittest.TestCase):
    def test_fetches_remaining_children_when_truncated(self):
        client = FakeClient(
            [parent(kids=children(completed=1), kids_truncated=True)],
            extra_children=children(completed=1, started=3),
        )

        run(client)

        self.assertEqual(client.labels_added(), [label_id("025-049%")])

    def test_fetches_remaining_labels_when_truncated(self):
        client = FakeClient(
            [parent(kids=children(completed=1), labels_truncated=True)],
            extra_labels=[applied(label_id("100% complete"), GROUP_ID)],
        )

        report = run(client)

        self.assertEqual(report.parents_skipped, 1)
        self.assertEqual(client.mutations, [])

    def test_team_filter_is_applied(self):
        recorded = []

        class RecordingClient(FakeClient):
            def paginate(self, query, variables, path, page_size=50):
                if tuple(path) == ("issues",):
                    recorded.append(variables["filter"])
                return super().paginate(query, variables, path, page_size)

        run(RecordingClient([]), team_key="ENG")

        self.assertEqual(
            recorded[0], {"children": {"some": {}}, "team": {"key": {"eq": "ENG"}}}
        )


class ForeignLabelGroupTests(unittest.TestCase):
    """Other label groups are counted so the operator can fix the view setting."""

    def test_labels_from_other_groups_are_counted(self):
        client = FakeClient(
            [
                parent(
                    "p1",
                    kids=children(completed=1),
                    labels=[applied("label-p1", "other-group")],
                ),
                parent("p2", kids=children(completed=1), labels=[applied(UNRELATED_LABEL)]),
            ]
        )

        with self.assertLogs("parent_progress_sync.sync", level="INFO"):
            report = run(client)

        self.assertEqual(report.parents_in_other_label_groups, 1)

    def test_managed_labels_are_excluded_by_id_not_by_parent(self):
        # Managed labels are recognised by ID so the check stays correct even
        # when the group ID is a dry-run placeholder.
        client = FakeClient(
            [parent(kids=children(completed=2), labels=[label_id("100% complete")])]
        )

        report = run(client, dry_run=True)

        self.assertEqual(report.parents_in_other_label_groups, 0)

    def test_managed_labels_are_not_counted_as_foreign(self):
        client = FakeClient(
            [parent(kids=children(completed=2), labels=[label_id("100% complete")])]
        )

        report = run(client)

        self.assertEqual(report.parents_in_other_label_groups, 0)

    def test_warning_names_the_group_to_group_by(self):
        client = FakeClient(
            [parent(kids=children(completed=1), labels=[applied("x", "other-group")])]
        )

        with self.assertLogs("parent_progress_sync.sync", level="WARNING") as logs:
            run(client)

        message = "\n".join(logs.output)
        self.assertIn("other label groups", message)
        self.assertIn("Parent progress", message)

    def test_no_warning_without_foreign_groups(self):
        client = FakeClient([parent(kids=children(completed=1))])

        with self.assertLogs("parent_progress_sync.sync", level="INFO") as logs:
            run(client)

        self.assertNotIn("WARNING", "\n".join(logs.output))


class LoggingTests(unittest.TestCase):
    def test_info_logs_omit_issue_titles(self):
        client = FakeClient([parent(title="Secret codename", kids=children(completed=1))])

        with self.assertLogs("parent_progress_sync.sync", level="INFO") as logs:
            run(client)

        self.assertNotIn("Secret codename", "\n".join(logs.output))
        self.assertIn("+100% complete", "\n".join(logs.output))

    def test_debug_logs_include_titles_during_cleanup(self):
        client = FakeClient([parent(title="[025%] Secret codename", kids=children(completed=1))])

        with self.assertLogs("parent_progress_sync.sync", level="DEBUG") as logs:
            run(client, cleanup_legacy_prefixes=True)

        self.assertIn("Secret codename", "\n".join(logs.output))


class FailureTests(unittest.TestCase):
    def test_failed_mutation_raises(self):
        client = FakeClient([parent(kids=children(completed=1))])
        client.execute = lambda query, variables=None: {"issueAddLabel": {"success": False}}

        with self.assertRaises(RuntimeError):
            run(client)


if __name__ == "__main__":
    unittest.main()

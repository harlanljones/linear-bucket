import unittest

from parent_progress_sync.config import Config
from parent_progress_sync.sync import ProgressSync, count_progress


def child(state_type):
    return {"id": f"child-{state_type}", "state": {"type": state_type}}


def parent(issue_id, title, children, has_next_page=False):
    return {
        "id": issue_id,
        "identifier": issue_id.upper(),
        "title": title,
        "children": {"pageInfo": {"hasNextPage": has_next_page}, "nodes": children},
    }


class FakeClient:
    """Stands in for LinearClient, recording mutations instead of sending them."""

    def __init__(self, parents, extra_children=None):
        self._parents = parents
        self._extra_children = extra_children or []
        self.mutations = []
        self.paginate_calls = []

    def paginate(self, query, variables, path, page_size=50):
        self.paginate_calls.append((tuple(path), dict(variables), page_size))
        if tuple(path) == ("issues",):
            return iter(self._parents)
        return iter(self._extra_children)

    def execute(self, query, variables=None):
        self.mutations.append(dict(variables or {}))
        return {"issueUpdate": {"success": True}}


def config(**overrides):
    return Config(api_key="k", **overrides)


class CountProgressTests(unittest.TestCase):
    def test_counts_completed_children(self):
        progress = count_progress([child("completed"), child("started"), child("backlog")])
        self.assertEqual((progress.completed, progress.total), (1, 3))

    def test_canceled_children_are_excluded(self):
        progress = count_progress([child("completed"), child("canceled")])
        self.assertEqual((progress.completed, progress.total), (1, 1))
        self.assertEqual(progress.percent, 100)

    def test_state_type_casing_is_ignored(self):
        self.assertEqual(count_progress([{"state": {"type": "Completed"}}]).completed, 1)

    def test_missing_state_counts_as_incomplete(self):
        self.assertEqual(count_progress([{"id": "x"}]).completed, 0)


class ProgressSyncTests(unittest.TestCase):
    def test_adds_prefix_to_parent(self):
        client = FakeClient([parent("p1", "Ship login", [child("completed"), child("started")])])

        report = ProgressSync(client, config()).run()

        self.assertEqual(report.parents_scanned, 1)
        self.assertEqual(report.parents_updated, 1)
        self.assertEqual(client.mutations, [{"id": "p1", "title": "[050%] Ship login"}])

    def test_replaces_a_stale_prefix(self):
        client = FakeClient(
            [parent("p1", "[025%] Ship login", [child("completed"), child("completed")])]
        )

        ProgressSync(client, config()).run()

        self.assertEqual(client.mutations, [{"id": "p1", "title": "[100%] Ship login"}])

    def test_skips_parents_already_correct(self):
        client = FakeClient([parent("p1", "[050%] Ship login", [child("completed"), child("started")])])

        report = ProgressSync(client, config()).run()

        self.assertEqual(report.parents_skipped, 1)
        self.assertEqual(client.mutations, [])

    def test_drops_prefix_when_every_child_is_canceled(self):
        client = FakeClient([parent("p1", "[050%] Ship login", [child("canceled")])])

        ProgressSync(client, config()).run()

        self.assertEqual(client.mutations, [{"id": "p1", "title": "Ship login"}])

    def test_no_write_when_all_children_canceled_and_no_prefix(self):
        client = FakeClient([parent("p1", "Ship login", [child("canceled")])])

        report = ProgressSync(client, config()).run()

        self.assertEqual(report.parents_skipped, 1)
        self.assertEqual(client.mutations, [])

    def test_dry_run_reports_without_writing(self):
        client = FakeClient([parent("p1", "Ship login", [child("completed")])])

        report = ProgressSync(client, config(dry_run=True)).run()

        self.assertEqual(report.parents_updated, 1)
        self.assertEqual(report.updates[0].new_title, "[100%] Ship login")
        self.assertEqual(client.mutations, [])
        self.assertIn("would update 1", report.summary())

    def test_fetches_remaining_children_when_page_is_truncated(self):
        client = FakeClient(
            [parent("p1", "Ship login", [child("completed")], has_next_page=True)],
            extra_children=[child("completed"), child("started"), child("started")],
        )

        ProgressSync(client, config()).run()

        self.assertEqual(client.paginate_calls[1][0], ("issue", "children"))
        self.assertEqual(client.mutations, [{"id": "p1", "title": "[033%] Ship login"}])

    def test_team_filter_is_applied(self):
        client = FakeClient([])

        ProgressSync(client, config(team_key="ENG")).run()

        _, variables, page_size = client.paginate_calls[0]
        self.assertEqual(
            variables["filter"],
            {"children": {"some": {}}, "team": {"key": {"eq": "ENG"}}},
        )
        self.assertEqual(page_size, 50)

    def test_failed_mutation_raises(self):
        client = FakeClient([parent("p1", "Ship login", [child("completed")])])
        client.execute = lambda query, variables=None: {"issueUpdate": {"success": False}}

        with self.assertRaises(RuntimeError):
            ProgressSync(client, config()).run()


if __name__ == "__main__":
    unittest.main()

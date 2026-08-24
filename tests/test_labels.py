import unittest

from parent_progress_sync.buckets import BUCKET_NAMES, DEFAULT_GROUP_NAME
from parent_progress_sync.labels import LabelCatalog, LabelError

GROUP_ID = "group-1"


def group_label(name=DEFAULT_GROUP_NAME, label_id=GROUP_ID, is_group=True, parent=None):
    return {"id": label_id, "name": name, "isGroup": is_group, "parent": parent}


def bucket_label(name, label_id=None, parent_id=GROUP_ID):
    parent = {"id": parent_id, "name": DEFAULT_GROUP_NAME} if parent_id else None
    return {"id": label_id or f"label-{name}", "name": name, "isGroup": False, "parent": parent}


def full_group():
    return [group_label(), *(bucket_label(name) for name in BUCKET_NAMES)]


class FakeClient:
    """Serves label queries from a flat list and records created labels."""

    def __init__(self, labels):
        self._labels = list(labels)
        self.created = []

    def paginate(self, query, variables, path, page_size=50):
        if "names" in variables:
            names = set(variables["names"])
            return iter([label for label in self._labels if label["name"] in names])
        group_id = variables["groupId"]
        return iter(
            [
                label
                for label in self._labels
                if (label.get("parent") or {}).get("id") == group_id
            ]
        )

    def execute(self, query, variables=None):
        payload = (variables or {})["input"]
        self.created.append(payload)
        created = {
            "id": f"new-{payload['name']}",
            "name": payload["name"],
            "isGroup": payload.get("isGroup", False),
            "parent": {"id": payload["parentId"], "name": DEFAULT_GROUP_NAME}
            if payload.get("parentId")
            else None,
        }
        self._labels.append(created)
        return {"issueLabelCreate": {"success": True, "issueLabel": created}}


def catalog(client, group_name=DEFAULT_GROUP_NAME):
    return LabelCatalog(client, group_name)


class ResolveTests(unittest.TestCase):
    def test_resolves_existing_group(self):
        client = FakeClient(full_group())

        labels = catalog(client).resolve(bootstrap=False, dry_run=False)

        self.assertEqual(labels.group_id, GROUP_ID)
        self.assertEqual(set(labels.bucket_ids), set(BUCKET_NAMES))
        self.assertEqual(client.created, [])

    def test_managed_ids_include_unknown_children(self):
        # A retired bucket left in the group must still be removable.
        client = FakeClient([*full_group(), bucket_label("legacy band", "label-legacy")])

        labels = catalog(client).resolve(bootstrap=False, dry_run=False)

        self.assertIn("label-legacy", labels.managed_ids)

    def test_missing_group_without_bootstrap_is_an_error(self):
        with self.assertRaises(LabelError) as caught:
            catalog(FakeClient([])).resolve(bootstrap=False, dry_run=False)
        self.assertIn("--no-bootstrap-labels", str(caught.exception))

    def test_missing_buckets_without_bootstrap_is_an_error(self):
        client = FakeClient([group_label(), bucket_label(BUCKET_NAMES[0])])

        with self.assertRaises(LabelError) as caught:
            catalog(client).resolve(bootstrap=False, dry_run=False)
        self.assertIn("missing", str(caught.exception))

    def test_bootstrap_creates_group_and_buckets(self):
        client = FakeClient([])

        labels = catalog(client).resolve(bootstrap=True, dry_run=False)

        self.assertEqual(client.created[0], {"name": DEFAULT_GROUP_NAME, "isGroup": True})
        self.assertEqual(len(client.created), 1 + len(BUCKET_NAMES))
        for payload in client.created[1:]:
            self.assertEqual(payload["parentId"], f"new-{DEFAULT_GROUP_NAME}")
        self.assertEqual(set(labels.bucket_ids), set(BUCKET_NAMES))

    def test_bootstrap_only_creates_what_is_missing(self):
        existing = [group_label(), *(bucket_label(name) for name in BUCKET_NAMES[:3])]
        client = FakeClient(existing)

        catalog(client).resolve(bootstrap=True, dry_run=False)

        self.assertEqual([payload["name"] for payload in client.created], list(BUCKET_NAMES[3:]))

    def test_dry_run_bootstrap_writes_nothing(self):
        client = FakeClient([])

        labels = catalog(client).resolve(bootstrap=True, dry_run=True)

        self.assertEqual(client.created, [])
        self.assertTrue(labels.group_id.startswith("<would-create:"))


class CollisionsFailEvenWhenBootstrapping(unittest.TestCase):
    """Self-initializing must not mean papering over an ambiguous workspace."""

    def assert_refuses(self, labels):
        with self.assertRaises(LabelError):
            catalog(FakeClient(labels)).resolve(bootstrap=True, dry_run=False)

    def test_duplicate_group_names(self):
        self.assert_refuses([group_label(), group_label(label_id="group-2")])

    def test_group_name_held_by_plain_label(self):
        self.assert_refuses([group_label(is_group=False)])

    def test_nested_group(self):
        self.assert_refuses([group_label(parent={"id": "other", "name": "Somewhere"})])

    def test_bucket_name_taken_outside_the_group(self):
        self.assert_refuses(
            [*full_group(), bucket_label(BUCKET_NAMES[0], "loose", parent_id=None)]
        )

    def test_nothing_is_created_when_validation_fails(self):
        client = FakeClient([group_label(is_group=False)])

        with self.assertRaises(LabelError):
            catalog(client).resolve(bootstrap=True, dry_run=False)

        self.assertEqual(client.created, [])


class CollisionTests(unittest.TestCase):
    def test_duplicate_group_names_rejected(self):
        client = FakeClient([group_label(), group_label(label_id="group-2")])

        with self.assertRaises(LabelError) as caught:
            catalog(client).resolve(bootstrap=False, dry_run=False)
        self.assertIn("unambiguous", str(caught.exception))

    def test_group_name_taken_by_plain_label(self):
        client = FakeClient([group_label(is_group=False)])

        with self.assertRaises(LabelError) as caught:
            catalog(client).resolve(bootstrap=False, dry_run=False)
        self.assertIn("not a label group", str(caught.exception))

    def test_nested_group_rejected(self):
        client = FakeClient([group_label(parent={"id": "other", "name": "Somewhere"})])

        with self.assertRaises(LabelError) as caught:
            catalog(client).resolve(bootstrap=False, dry_run=False)
        self.assertIn("nested", str(caught.exception))

    def test_bucket_name_taken_outside_the_group(self):
        client = FakeClient([*full_group(), bucket_label(BUCKET_NAMES[0], "loose", parent_id=None)])

        with self.assertRaises(LabelError) as caught:
            catalog(client).resolve(bootstrap=False, dry_run=False)
        self.assertIn("outside the managed group", str(caught.exception))

    def test_bucket_name_taken_under_a_different_group(self):
        other = {"id": "other-group", "name": "Other"}
        colliding = {
            "id": "loose",
            "name": BUCKET_NAMES[1],
            "isGroup": False,
            "parent": other,
        }
        client = FakeClient([*full_group(), colliding])

        with self.assertRaises(LabelError):
            catalog(client).resolve(bootstrap=False, dry_run=False)

    def test_custom_group_name_is_honoured(self):
        labels = [
            group_label(name="Progress", label_id="g2"),
            *(bucket_label(name, parent_id="g2") for name in BUCKET_NAMES),
        ]

        resolved = catalog(FakeClient(labels), "Progress").resolve(bootstrap=False, dry_run=False)

        self.assertEqual(resolved.group_id, "g2")


if __name__ == "__main__":
    unittest.main()

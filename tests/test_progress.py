import unittest

from parent_progress_sync.progress import (
    Progress,
    apply_prefix,
    compute_percent,
    format_prefix,
    parse_prefix,
    strip_prefix,
)


class ComputePercentTests(unittest.TestCase):
    def test_basic_ratios(self):
        self.assertEqual(compute_percent(0, 4), 0)
        self.assertEqual(compute_percent(1, 4), 25)
        self.assertEqual(compute_percent(3, 4), 75)
        self.assertEqual(compute_percent(4, 4), 100)

    def test_rounds_to_nearest(self):
        self.assertEqual(compute_percent(1, 3), 33)
        self.assertEqual(compute_percent(2, 3), 67)

    def test_never_rounds_up_to_complete(self):
        self.assertEqual(compute_percent(999, 1000), 99)

    def test_never_rounds_down_to_zero(self):
        self.assertEqual(compute_percent(1, 1000), 1)

    def test_no_children_is_zero(self):
        self.assertEqual(compute_percent(0, 0), 0)

    def test_rejects_impossible_counts(self):
        with self.assertRaises(ValueError):
            compute_percent(3, 2)
        with self.assertRaises(ValueError):
            compute_percent(-1, 2)

    def test_progress_dataclass_exposes_percent(self):
        self.assertEqual(Progress(completed=1, total=2).percent, 50)


class PrefixTests(unittest.TestCase):
    def test_format_is_zero_padded(self):
        self.assertEqual(format_prefix(0), "[000%] ")
        self.assertEqual(format_prefix(7), "[007%] ")
        self.assertEqual(format_prefix(42), "[042%] ")
        self.assertEqual(format_prefix(100), "[100%] ")

    def test_format_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            format_prefix(101)

    def test_apply_adds_prefix(self):
        self.assertEqual(apply_prefix("Ship login", 42), "[042%] Ship login")

    def test_apply_replaces_existing_prefix(self):
        self.assertEqual(apply_prefix("[007%] Ship login", 42), "[042%] Ship login")

    def test_apply_is_idempotent(self):
        once = apply_prefix("Ship login", 42)
        self.assertEqual(apply_prefix(once, 42), once)

    def test_strip_leaves_unprefixed_titles_alone(self):
        self.assertEqual(strip_prefix("Ship login"), "Ship login")
        self.assertEqual(strip_prefix("[WIP] Ship login"), "[WIP] Ship login")
        self.assertEqual(strip_prefix("50% done"), "50% done")

    def test_strip_removes_only_one_prefix(self):
        self.assertEqual(strip_prefix("[042%] [007%] Ship login"), "[007%] Ship login")

    def test_parse_prefix(self):
        self.assertEqual(parse_prefix("[042%] Ship login"), 42)
        self.assertIsNone(parse_prefix("Ship login"))
        self.assertIsNone(parse_prefix("[42%] Ship login"))

    def test_descending_title_sort_matches_descending_progress(self):
        titles = [apply_prefix(f"Issue {pct}", pct) for pct in (0, 7, 42, 100)]
        self.assertEqual(sorted(titles, reverse=True), list(reversed(titles)))


if __name__ == "__main__":
    unittest.main()

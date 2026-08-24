import unittest

from parent_progress_sync.buckets import BUCKET_NAMES, BUCKETS, bucket_for


class BucketBoundaryTests(unittest.TestCase):
    def test_every_percentage_maps_to_exactly_one_bucket(self):
        for percent in range(101):
            matches = [bucket for bucket in BUCKETS if bucket.contains(percent)]
            self.assertEqual(len(matches), 1, f"{percent}% matched {len(matches)} buckets")

    def test_boundaries(self):
        cases = {
            0: "000% not started",
            1: "001-024%",
            24: "001-024%",
            25: "025-049%",
            49: "025-049%",
            50: "050-074%",
            74: "050-074%",
            75: "075-099%",
            99: "075-099%",
            100: "100% complete",
        }
        for percent, expected in cases.items():
            self.assertEqual(bucket_for(percent).name, expected, f"at {percent}%")

    def test_bands_are_contiguous(self):
        for lower, upper in zip(BUCKETS, BUCKETS[1:]):
            self.assertEqual(upper.low, lower.high + 1)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            bucket_for(101)
        with self.assertRaises(ValueError):
            bucket_for(-1)

    def test_names_sort_in_progress_order(self):
        # Linear orders labels alphabetically, so the zero-padded numeric names
        # must already be in progress order.
        self.assertEqual(sorted(BUCKET_NAMES), list(BUCKET_NAMES))

    def test_names_are_unique(self):
        self.assertEqual(len(set(BUCKET_NAMES)), len(BUCKET_NAMES))


if __name__ == "__main__":
    unittest.main()

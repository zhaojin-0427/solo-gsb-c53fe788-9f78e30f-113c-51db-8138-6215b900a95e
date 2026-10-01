"""Regression tests for frozen execution batches and revision semantics."""

from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path

from app.execution import evaluate_warning
from app.storage import RevisionConflictError, Store


@dataclass(frozen=True)
class FakeSortie:
    id: str


def fake_scanner(sorties, offsets, safety_radius, safety_height, not_before):
    # Deterministic pair conflict used to assert effective offsets and clipping.
    if offsets == {"A": 0, "B": 1}:
        return [{
            "sortie_a": "A",
            "sortie_b": "B",
            "time_start": max(0.0, float(not_before)),
            "time_end": 6.0,
            "witness_time": max(float(not_before), 5.5),
            "a_pos": [0, 0, 0],
            "b_pos": [0, 0, 0],
            "horizontal_distance": 0.0,
            "vertical_distance": 0.0,
        }]
    return []


class ExecutionWarningTests(unittest.TestCase):
    def test_reported_planned_and_overdue_classification(self):
        sorties = [FakeSortie("A"), FakeSortie("B"), FakeSortie("C")]
        planned = {"A": 0, "B": 2, "C": 4}
        reports = {"A": {"actual_offset": 1, "revision": 1}}

        warning = evaluate_warning(
            sorties, planned, reports, current_t=3,
            safety_radius=10, safety_height=10,
            conflict_scanner=fake_scanner,
        )

        statuses = {s["sortie_id"]: s for s in warning["sorties"]}
        self.assertEqual(statuses["A"]["status"], "reported")
        self.assertEqual(statuses["A"]["deviation_seconds"], 1)
        self.assertEqual(statuses["B"]["status"], "overdue")
        self.assertIsNone(statuses["B"]["used_offset"])
        self.assertEqual(statuses["C"]["status"], "planned")
        self.assertEqual(statuses["C"]["used_offset"], 4)
        self.assertTrue(warning["incomplete"])
        self.assertFalse(warning["complete"])
        self.assertEqual(warning["deviations"], {"A": 1})
        self.assertEqual(warning["reported_actual_offsets"], {"A": 1})

    def test_conflict_scanner_receives_t_and_complete_prediction(self):
        sorties = [FakeSortie("A"), FakeSortie("B")]
        planned = {"A": 0, "B": 2}
        reports = {"A": {"actual_offset": 0, "revision": 1},
                   "B": {"actual_offset": 1, "revision": 2}}
        warning = evaluate_warning(
            sorties, planned, reports, current_t=5,
            safety_radius=10, safety_height=10,
            conflict_scanner=fake_scanner,
        )
        self.assertFalse(warning["incomplete"])
        self.assertEqual(warning["first_conflict_time"], 5.0)
        self.assertEqual(warning["conflicts"][0]["time_start"], 5.0)


class StoreBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "console.db"
        self.store = Store(self.db)
        self.snapshot = {
            "input": {"sorties": [{"id": "A"}, {"id": "B"}]},
            "planned_offsets": {"A": 0, "B": 2},
        }
        self.bid = self.store.create_batch(
            1, "batch", self.snapshot, 10.0, 10.0,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_idempotent_retry_and_revision_conflicts(self):
        reports = [{"sortie_id": "A", "actual_offset": 3, "revision": 1}]
        self.assertEqual(self.store.apply_batch_reports(self.bid, reports), {"A": "stored"})
        self.assertEqual(self.store.apply_batch_reports(self.bid, reports), {"A": "idempotent"})

        with self.assertRaises(RevisionConflictError):
            self.store.apply_batch_reports(
                self.bid, [{"sortie_id": "A", "actual_offset": 4, "revision": 1}]
            )
        with self.assertRaises(RevisionConflictError):
            self.store.apply_batch_reports(
                self.bid, [{"sortie_id": "A", "actual_offset": 2, "revision": 0}]
            )
        # Failed requests must not mutate the stored first report.
        stored = self.store.get_batch(self.bid)["reports"]["A"]
        self.assertEqual(stored["actual_offset"], 3)
        self.assertEqual(stored["revision"], 1)

        self.assertEqual(
            self.store.apply_batch_reports(
                self.bid, [{"sortie_id": "A", "actual_offset": 5, "revision": 2}]
            ),
            {"A": "stored"},
        )
        stored = self.store.get_batch(self.bid)["reports"]["A"]
        self.assertEqual((stored["actual_offset"], stored["revision"]), (5, 2))

    def test_unknown_and_duplicate_sortie_reports_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.apply_batch_reports(
                self.bid, [{"sortie_id": "X", "actual_offset": 1, "revision": 1}]
            )
        with self.assertRaises(ValueError):
            self.store.apply_batch_reports(
                self.bid, [{"sortie_id": "A", "actual_offset": 1, "revision": 2}]
            )
        with self.assertRaises(ValueError):
            self.store.apply_batch_reports(self.bid, [
                {"sortie_id": "A", "actual_offset": 1, "revision": 1},
                {"sortie_id": "A", "actual_offset": 2, "revision": 2},
            ])

    def test_concurrent_reports_keep_highest_revision(self):
        def report(revision):
            try:
                self.store.apply_batch_reports(
                    self.bid,
                    [{"sortie_id": "B", "actual_offset": revision, "revision": revision}],
                )
            except RevisionConflictError:
                # Only one ordering can win under concurrent submissions;
                # final stored revision is asserted below.
                pass

        threads = [threading.Thread(target=report, args=(rev,)) for rev in range(1, 21)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Threads may apply revisions in arrival order (1..20) or race at the
        # SQL predicate; either way the durable state must end at revision 20.
        stored = self.store.get_batch(self.bid)["reports"]["B"]
        self.assertEqual(stored["revision"], 20)
        self.assertEqual(stored["actual_offset"], 20)

    def test_batch_snapshot_reloads_deterministically(self):
        self.store.apply_batch_reports(
            self.bid, [{"sortie_id": "A", "actual_offset": 4, "revision": 1}]
        )
        first = self.store.get_batch(self.bid)
        second = Store(self.db).get_batch(self.bid)
        self.assertEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(first["reports"]["A"], second["reports"]["A"])
        self.assertEqual(first["safety_radius"], second["safety_radius"])


if __name__ == "__main__":
    unittest.main()

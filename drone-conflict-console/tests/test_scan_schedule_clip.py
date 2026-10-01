"""Test continuous-time conflict scan clipping from execution time t."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# scan_schedule only needs geometry, but app.solver imports OR-Tools at module
# load time. Unit environments without the full image can provide a tiny stub.
if "ortools" not in sys.modules:
    for name in ("ortools", "ortools.sat", "ortools.sat.python",
                 "ortools.sat.python.cp_model"):
        sys.modules[name] = types.ModuleType(name)

from app.model import Waypoint, make_sortie  # noqa: E402
from app.solver import scan_schedule  # noqa: E402


class ScanScheduleClipTests(unittest.TestCase):
    def test_conflict_is_clipped_to_current_t(self):
        a = make_sortie("A", 100, 0, [
            Waypoint(0, 0, 20, 0),
            Waypoint(100, 0, 20, 10),
        ])
        b = make_sortie("B", 1, 20, [
            Waypoint(50, -50, 20, 0),
            Waypoint(50, 50, 20, 10),
        ])

        full = scan_schedule([a, b], {"A": 0, "B": 1}, 10, 10)
        clipped = scan_schedule([a, b], {"A": 0, "B": 1}, 10, 10, not_before=5.5)

        self.assertEqual(len(full), 1)
        self.assertEqual((full[0]["time_start"], full[0]["time_end"]), (5.0, 6.0))
        self.assertEqual(len(clipped), 1)
        self.assertEqual((clipped[0]["time_start"], clipped[0]["time_end"]), (5.5, 6.0))
        self.assertEqual(clipped[0]["witness_time"], 5.5)
        self.assertEqual(clipped[0]["a_pos"], [55.0, 0.0, 20.0])
        self.assertEqual(clipped[0]["b_pos"], [50.0, -5.0, 20.0])
        self.assertEqual(clipped[0]["horizontal_distance"], 7.071068)
        self.assertEqual(clipped[0]["vertical_distance"], 0.0)

    def test_excluded_sorties_have_no_pair_scan(self):
        a = make_sortie("A", 100, 0, [
            Waypoint(0, 0, 20, 0),
            Waypoint(100, 0, 20, 10),
        ])
        b = make_sortie("B", 1, 20, [
            Waypoint(50, -50, 20, 0),
            Waypoint(50, 50, 20, 10),
        ])
        self.assertEqual(scan_schedule([a, b], {"A": 0}, 10, 10, not_before=0), [])


if __name__ == "__main__":
    unittest.main()

"""Execution-time deviation warnings for a frozen plan batch."""

from __future__ import annotations


def evaluate_warning(sorties, planned_offsets: dict[str, int],
                     reports: dict[str, dict], current_t: int,
                     safety_radius: float, safety_height: float,
                     conflict_scanner) -> dict:
    """Build the effective execution schedule and scan future conflicts.

    Status meanings:

    * ``reported`` — use the reported actual take-off offset;
    * ``planned`` — no report yet and the planned take-off is not before ``t``;
    * ``overdue`` — no report yet and its planned take-off is already before
      ``t``. It is excluded from prediction, making the prediction incomplete.
    """
    effective_offsets: dict[str, int] = {}
    statuses: list[dict] = []
    overdue = False
    reported_actual: dict[str, int] = {}

    for sortie in sorties:
        sid = sortie.id
        planned = int(planned_offsets[sid])
        report = reports.get(sid)
        if report is not None:
            actual = int(report["actual_offset"])
            revision = int(report["revision"])
            reported_actual[sid] = actual
            effective_offsets[sid] = actual
            statuses.append({
                "sortie_id": sid,
                "status": "reported",
                "planned_offset": planned,
                "actual_offset": actual,
                "revision": revision,
                "deviation_seconds": actual - planned,
                "used_offset": actual,
            })
        elif planned >= current_t:
            effective_offsets[sid] = planned
            statuses.append({
                "sortie_id": sid,
                "status": "planned",
                "planned_offset": planned,
                "actual_offset": None,
                "revision": None,
                "deviation_seconds": None,
                "used_offset": planned,
            })
        else:
            overdue = True
            statuses.append({
                "sortie_id": sid,
                "status": "overdue",
                "planned_offset": planned,
                "actual_offset": None,
                "revision": None,
                "deviation_seconds": None,
                "used_offset": None,
            })

    conflicts = conflict_scanner(sorties, effective_offsets, safety_radius,
                                 safety_height, current_t)
    first_conflict_time = conflicts[0]["time_start"] if conflicts else None
    return {
        "t": current_t,
        "incomplete": overdue,
        "complete": not overdue,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "first_conflict_time": first_conflict_time,
        "sorties": statuses,
        "reported_actual_offsets": reported_actual,
        "deviations": {
            item["sortie_id"]: item["deviation_seconds"]
            for item in statuses
            if item["status"] == "reported"
        },
    }

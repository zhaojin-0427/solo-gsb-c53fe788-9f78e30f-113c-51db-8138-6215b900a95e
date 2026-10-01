"""FastAPI application: import, analyse, solve and persist drone scenarios."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .execution import evaluate_warning
from .model import Waypoint, make_sortie
from .schemas import (
    BatchCreateRequest,
    BatchReportRequest,
    ImportPayload,
    LockEntry,
    SaveRequest,
    SolveRequest,
)
from .solver import (
    PairConstraint,
    build_pair_constraints,
    run_solve,
    scan_schedule,
)
from .storage import RevisionConflictError, Store

DB_PATH = os.environ.get("DCC_DB_PATH", "data/console.db")
store: Store | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = Store(DB_PATH)
    yield


app = FastAPI(title="无人机航迹冲突消解台", version="1.0", lifespan=lifespan)


def _build_sorties(payload: ImportPayload | dict):
    if isinstance(payload, ImportPayload):
        data = payload
    else:
        data = ImportPayload.model_validate(payload)
    sorties = []
    for s in data.sorties:
        wps = [Waypoint(w.x, w.y, w.z, w.t) for w in s.waypoints]
        sorties.append(make_sortie(s.id, s.weight, s.window_hi, wps, 0))
    return sorties, data.safety_radius, data.safety_height


def _pair_constraints_dto(pcs: list[PairConstraint]) -> list[dict]:
    return [
        {"sortie_a": pc.sortie_a, "sortie_b": pc.sortie_b,
         "forbidden_offsets": [[lo, hi] for lo, hi in pc.intervals]}
        for pc in pcs
    ]


def _scenario_summary(sid: int, sorties, safety_radius, safety_height,
                      pcs, solve_result=None, earliest_conflicts=None) -> dict:
    out = {
        "scenario_id": sid,
        "safety_radius": safety_radius,
        "safety_height": safety_height,
        "sorties": [
            {
                "id": s.id,
                "weight": s.weight,
                "window_lo": s.window_lo,
                "window_hi": s.window_hi,
                "flight_end": s.flight_end,
                "waypoints": [
                    {"x": w.x, "y": w.y, "z": w.z, "t": w.t} for w in s.waypoints
                ],
            }
            for s in sorties
        ],
        "pair_constraints": _pair_constraints_dto(pcs),
        "earliest_conflicts": earliest_conflicts,
    }
    if solve_result is not None:
        out["solution"] = solve_result
    return out


def _sortie_summary(sorties) -> list[dict]:
    return [
        {
            "id": s.id,
            "weight": s.weight,
            "window_lo": s.window_lo,
            "window_hi": s.window_hi,
            "flight_end": s.flight_end,
            "waypoints": [
                {"x": w.x, "y": w.y, "z": w.z, "t": w.t} for w in s.waypoints
            ],
        }
        for s in sorties
    ]


def _load_batch(bid: int):
    row = store.get_batch(bid)
    if row is None:
        raise HTTPException(404, "batch not found")
    payload = ImportPayload.model_validate(row["snapshot"]["input"])
    sorties, _, _ = _build_sorties(payload)
    return row, sorties


def _batch_summary(row: dict, sorties, warning: dict | None = None) -> dict:
    reports = {
        sid: {
            "actual_offset": int(r["actual_offset"]),
            "revision": int(r["revision"]),
            "reported_at": r["reported_at"],
        }
        for sid, r in row.get("reports", {}).items()
    }
    out = {
        "batch_id": row["id"],
        "source_scenario_id": row["source_scenario_id"],
        "name": row["name"] or f"执行批次 #{row['id']}",
        "created_at": row["created_at"],
        "safety_radius": row["safety_radius"],
        "safety_height": row["safety_height"],
        "planned_offsets": dict(row["snapshot"]["planned_offsets"]),
        "reports": reports,
        "sorties": _sortie_summary(sorties),
    }
    if warning is not None:
        out["warning"] = warning
    return out


def _batch_list_item(row: dict) -> dict:
    planned = dict(row["snapshot"].get("planned_offsets", {}))
    return {
        "batch_id": row["id"],
        "source_scenario_id": row["source_scenario_id"],
        "name": row["name"] or f"执行批次 #{row['id']}",
        "safety_radius": row["safety_radius"],
        "safety_height": row["safety_height"],
        "created_at": row["created_at"],
        "sortie_count": row["sortie_count"],
        "reported_count": row["reported_count"],
        "planned_offsets": planned,
        "sortie_ids": [s["id"] for s in row["snapshot"]["input"]["sorties"]],
    }


@app.post("/api/import")
def import_scenario(payload: ImportPayload):
    sorties, R, H = _build_sorties(payload)
    sid = store.create_scenario(payload.model_dump(), R, H)
    pcs = build_pair_constraints(sorties, R, H)
    earliest = scan_schedule(sorties, {s.id: 0 for s in sorties}, R, H)
    return _scenario_summary(sid, sorties, R, H, pcs, earliest_conflicts=earliest)


@app.get("/api/scenarios")
def list_scenarios():
    return {"scenarios": store.list_scenarios()}


@app.get("/api/scenarios/{sid}")
def get_scenario(sid: int):
    row = store.get_scenario(sid)
    if row is None:
        raise HTTPException(404, "scenario not found")
    payload = ImportPayload.model_validate(row["input"])
    sorties, _, _ = _build_sorties(payload)
    R = row["safety_radius"]
    H = row["safety_height"]
    pcs = build_pair_constraints(sorties, R, H)
    earliest = scan_schedule(sorties, {s.id: 0 for s in sorties}, R, H)
    return _scenario_summary(sid, sorties, R, H, pcs,
                             solve_result=row["result"],
                             earliest_conflicts=earliest)


@app.delete("/api/scenarios/{sid}")
def delete_scenario(sid: int):
    if not store.delete_scenario(sid):
        raise HTTPException(404, "scenario not found")
    return {"deleted": sid}


def _load(sid: int):
    row = store.get_scenario(sid)
    if row is None:
        raise HTTPException(404, "scenario not found")
    payload = ImportPayload.model_validate(row["input"])
    sorties, _, _ = _build_sorties(payload)
    return sorties, row, payload


@app.post("/api/scenarios/{sid}/solve")
def solve_scenario(sid: int, req: SolveRequest):
    sorties, row, payload = _load(sid)
    R = req.safety_radius if req.safety_radius is not None else row["safety_radius"]
    H = req.safety_height if req.safety_height is not None else row["safety_height"]
    if R <= 0 or H <= 0:
        raise HTTPException(400, "safety parameters must be positive")
    known = {s.id: s for s in sorties}
    locks: dict[str, int] = {}
    for lk in req.locks:
        if lk.sortie_id not in known:
            raise HTTPException(400, f"unknown sortie id in locks: {lk.sortie_id}")
        s = known[lk.sortie_id]
        if not (s.window_lo <= lk.offset <= s.window_hi):
            raise HTTPException(
                400,
                f"locked offset {lk.offset} for {lk.sortie_id} outside window "
                f"[{s.window_lo}, {s.window_hi}]",
            )
        locks[lk.sortie_id] = lk.offset

    pcs = build_pair_constraints(sorties, R, H)
    res = run_solve(sorties, pcs, locks, time_limit=req.time_limit)
    earliest = scan_schedule(sorties, {s.id: 0 for s in sorties}, R, H)
    solved_conflicts = []
    if res.feasible:
        solved_conflicts = scan_schedule(sorties, res.offsets, R, H)
        if solved_conflicts:
            raise HTTPException(500, "internal error: solved schedule still conflicts")

    result = {
        "feasible": res.feasible,
        "status": res.status,
        "weighted_delay": res.weighted_delay,
        "offsets": res.offsets,
        "locked": res.locked,
        "elapsed_seconds": round(res.elapsed_seconds, 4),
        "solved_conflicts": solved_conflicts,
        "cores": res.cores,
        "truncated": res.truncated,
    }
    store.save_result(sid, result, R, H)
    return _scenario_summary(sid, sorties, R, H, pcs,
                             solve_result=result,
                             earliest_conflicts=earliest)


@app.post("/api/scenarios/{sid}/save")
def save_scenario(sid: int, req: SaveRequest):
    sorties, row, payload = _load(sid)
    if row["result"] is None:
        raise HTTPException(400, "solve the scenario before saving a plan")
    store.save_result(sid, row["result"], row["safety_radius"], row["safety_height"],
                      name=req.name)
    return {"scenario_id": sid, "name": req.name, "saved": True}


@app.post("/api/scenarios/{sid}/batches")
def create_batch(sid: int, req: BatchCreateRequest):
    sorties, row, payload = _load(sid)
    result = row["result"]
    if result is None or not result.get("feasible"):
        raise HTTPException(400, "a feasible solved plan is required before creating a batch")
    offsets = result.get("offsets") or {}
    missing = sorted({s.id for s in sorties} - set(offsets))
    if missing:
        raise HTTPException(500, f"saved plan is missing offsets for {missing}")

    snapshot = {
        "input": payload.model_dump(),
        "planned_offsets": {s.id: int(offsets[s.id]) for s in sorties},
        "source_solution": {
            "status": result.get("status"),
            "weighted_delay": result.get("weighted_delay"),
        },
    }
    name = req.name or f"{row['name'] or f'方案 #{sid}'} 执行批次"
    bid = store.create_batch(sid, name, snapshot, row["safety_radius"], row["safety_height"])
    batch = store.get_batch(bid)
    return _batch_summary(batch, sorties)


@app.get("/api/batches")
def list_batches():
    return {"batches": [_batch_list_item(row) for row in store.list_batches()]}


@app.get("/api/batches/{bid}")
def get_batch(bid: int, t: int | None = Query(None, ge=0)):
    row, sorties = _load_batch(bid)
    warning = None
    if t is not None:
        warning = _evaluate_batch_warning(row, sorties, t)
    return _batch_summary(row, sorties, warning)


@app.post("/api/batches/{bid}/reports")
def report_batch(bid: int, req: BatchReportRequest):
    try:
        row, sorties = _load_batch(bid)
        outcomes = store.apply_batch_reports(
            bid,
            [item.model_dump() for item in req.reports],
        )
    except KeyError:
        raise HTTPException(404, "batch not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RevisionConflictError as exc:
        raise HTTPException(
            409,
            {
                "error": "revision_conflict",
                "sortie_id": exc.sortie_id,
                "submitted_revision": exc.submitted_revision,
                "stored_revision": exc.stored_revision,
            },
        )
    row = store.get_batch(bid)
    return {"batch_id": bid, "accepted": True, "outcomes": outcomes,
            "batch": _batch_summary(row, sorties)}


@app.get("/api/batches/{bid}/warning")
def batch_warning(bid: int, t: int = Query(..., ge=0)):
    row, sorties = _load_batch(bid)
    return {"batch_id": bid, "warning": _evaluate_batch_warning(row, sorties, t)}


@app.delete("/api/batches/{bid}")
def delete_batch(bid: int):
    if not store.delete_batch(bid):
        raise HTTPException(404, "batch not found")
    return {"deleted": bid}


def _evaluate_batch_warning(row: dict, sorties, t: int) -> dict:
    reports = row["reports"]
    earlier = [
        (sid, int(r["actual_offset"]))
        for sid, r in reports.items()
        if int(r["actual_offset"]) > t
    ]
    if earlier:
        sid, actual = sorted(earlier, key=lambda x: (x[1], x[0]))[0]
        raise HTTPException(
            400,
            f"current t={t} is earlier than reported actual take-off {actual} for {sid}",
        )
    warning = evaluate_warning(
        sorties, row["snapshot"]["planned_offsets"], reports, t,
        row["safety_radius"], row["safety_height"], scan_schedule,
    )
    return warning


@app.get("/api/health")
def health():
    return {"ok": True}


# Static front-end (served last so /api/* wins).
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="app")


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")

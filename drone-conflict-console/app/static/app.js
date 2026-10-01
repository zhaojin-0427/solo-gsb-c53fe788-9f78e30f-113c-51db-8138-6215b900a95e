/* 无人机航迹冲突消解台 — vanilla JS front-end */
"use strict";

const COLORS = [
  "#4da3ff", "#38d39f", "#ffb020", "#ff5d6c", "#b48bff",
  "#33e0d0", "#f57fb6", "#9dcc4a", "#e08a4a", "#6aa2ff",
];

const state = {
  scenario: null,        // full scenario DTO from server
  scenarioId: null,
  sorties: [],
  pairConstraints: [],
  earliestConflicts: [],
  solution: null,
  history: [],
  locks: {},             // sortieId -> offset (only when checked)
  time: 0,
  maxTime: 1,
  colorsById: {},
};

const $ = (id) => document.getElementById(id);

function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.classList.add("hidden"), ms);
}

function colorFor(id) {
  if (!(id in state.colorsById)) {
    state.colorsById[id] = COLORS[Object.keys(state.colorsById).length % COLORS.length];
  }
  return state.colorsById[id];
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

async function importScenario(payload) {
  const data = await api("/api/import", { method: "POST", body: JSON.stringify(payload) });
  loadScenarioDto(data);
  state.history = await fetchHistory();
  renderAll();
}

function loadScenarioDto(dto) {
  state.scenario = dto;
  state.scenarioId = dto.scenario_id;
  state.sorties = dto.sorties;
  state.pairConstraints = dto.pair_constraints || [];
  state.earliestConflicts = dto.earliest_conflicts || [];
  state.solution = dto.solution || null;
  state.locks = {};
  state.colorsById = {};
  state.sorties.forEach((s) => colorFor(s.id));
  $("safetyRadius").value = dto.safety_radius;
  $("safetyHeight").value = dto.safety_height;
  $("newSolveBtn").disabled = false;
  $("savePlanBtn").disabled = !(state.solution && state.solution.feasible);
  updateMaxTime();
  state.time = 0;
  $("timeScrub").value = 0;
  renderResultPanel();
  renderSortiePanel();
}

async function fetchHistory() {
  const d = await api("/api/scenarios");
  state.history = d.scenarios;
  renderHistory();
  return state.history;
}

async function solve(extraLocks) {
  const body = {
    safety_radius: parseFloat($("safetyRadius").value),
    safety_height: parseFloat($("safetyHeight").value),
    time_limit: parseFloat($("timeLimit").value) || 20,
    locks: extraLocks !== undefined ? extraLocks : collectLocks(),
  };
  try {
    const dto = await api(`/api/scenarios/${state.scenarioId}/solve`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    loadScenarioDto(dto);
    state.history = await fetchHistory();
    renderAll();
    if (dto.solution.feasible) toast("求解成功，已生成无冲突起飞时刻");
    else toast("当前条件无解，已列出极小无解架次集合", 4000);
  } catch (e) {
    toast("求解失败：" + e.message, 4200);
  }
}

function collectLocks() {
  return Object.entries(state.locks).map(([sortie_id, offset]) => ({ sortie_id, offset }));
}

// ---------------------------------------------------------------------------
// Geometry interpolation (mirrors the back-end model)
// ---------------------------------------------------------------------------

function segsOf(s) {
  const out = [];
  for (let i = 0; i < s.waypoints.length - 1; i++) {
    const a = s.waypoints[i], b = s.waypoints[i + 1];
    out.push({ a, b });
  }
  return out;
}

function positionAt(s, takeoff, tGlobal) {
  const tau = tGlobal - takeoff;
  if (tau < -1e-9 || tau > s.flight_end + 1e-9) return null;
  const wps = s.waypoints;
  for (let i = 0; i < wps.length - 1; i++) {
    const a = wps[i], b = wps[i + 1];
    if (tau >= a.t - 1e-9 && tau <= b.t + 1e-9) {
      const k = b.t === a.t ? 0 : Math.min(1, Math.max(0, (tau - a.t) / (b.t - a.t)));
      return {
        x: a.x + (b.x - a.x) * k,
        y: a.y + (b.y - a.y) * k,
        z: a.z + (b.z - a.z) * k,
      };
    }
  }
  const last = wps[wps.length - 1];
  return { x: last.x, y: last.y, z: last.z };
}

function updateMaxTime() {
  let m = 0;
  const off = currentOffsets();
  for (const s of state.sorties) {
    m = Math.max(m, (off[s.id] || 0) + s.flight_end);
  }
  // earliest track also matters
  for (const s of state.sorties) m = Math.max(m, s.flight_end);
  state.maxTime = Math.max(1, Math.ceil(m));
}

function currentOffsets() {
  const o = {};
  for (const s of state.sorties) {
    o[s.id] = state.solution && state.solution.feasible ? (state.solution.offsets[s.id] ?? 0) : 0;
  }
  return o;
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function renderSortiePanel() {
  const box = $("sortiesList");
  box.innerHTML = "";
  const solvedOff = state.solution && state.solution.feasible ? state.solution.offsets : null;
  for (const s of state.sorties) {
    const c = colorFor(s.id);
    const div = document.createElement("div");
    div.className = "sortie-item";
    const locked = s.id in state.locks;
    const lockVal = state.locks[s.id] ?? 0;
    div.innerHTML = `
      <div class="si-head">
        <span class="swatch" style="background:${c}"></span>
        <span class="si-name">${escapeHtml(s.id)}</span>
        <span class="si-meta">w=${s.weight} · 窗[0,${s.window_hi}] · 长${s.flight_end}s</span>
      </div>
      <div class="si-body">
        <span class="${solvedOff ? "solved-tag" : ""}">求解偏移: <b>${solvedOff ? solvedOff[s.id] : "—"}</b></span>
        <label class="lock-wrap">
          <input type="checkbox" ${locked ? "checked" : ""} data-lock-check="${s.id}" />
          锁定
          <input type="number" min="0" max="${s.window_hi}" step="1" value="${lockVal}"
                 data-lock-val="${s.id}" style="${locked ? "" : "opacity:.5"}" />
        </label>
      </div>`;
    box.appendChild(div);
  }
  box.querySelectorAll("[data-lock-check]").forEach((el) => {
    el.addEventListener("change", () => {
      const id = el.getAttribute("data-lock-check");
      if (el.checked) state.locks[id] = parseInt(el.parentElement.querySelector("[data-lock-val]").value, 10) || 0;
      else delete state.locks[id];
      renderSortiePanel();
    });
  });
  box.querySelectorAll("[data-lock-val]").forEach((el) => {
    el.addEventListener("input", () => {
      const id = el.getAttribute("data-lock-val");
      if (id in state.locks) state.locks[id] = parseInt(el.value, 10) || 0;
    });
  });
}

function renderHistory() {
  const box = $("historyList");
  box.innerHTML = "";
  if (!state.history.length) {
    box.innerHTML = `<div class="hint">暂无保存的方案。</div>`;
    return;
  }
  for (const h of state.history) {
    const div = document.createElement("div");
    div.className = "history-item";
    const title = h.name || `场景 #${h.id}`;
    div.innerHTML = `<span>#${h.id} ${escapeHtml(title)} ${h.has_result ? "✅" : "⭕"}</span>
      <span class="h-del" title="删除">🗑</span>`;
    div.addEventListener("click", async (e) => {
      if (e.target.classList.contains("h-del")) {
        if (!confirm(`删除场景 #${h.id}？`)) return;
        await api(`/api/scenarios/${h.id}`, { method: "DELETE" });
        toast("已删除");
        await fetchHistory();
        return;
      }
      try {
        const dto = await api(`/api/scenarios/${h.id}`);
        loadScenarioDto(dto);
        renderAll();
        toast("已载入历史方案（保留输入快照）");
      } catch (err) {
        toast("载入失败：" + err.message);
      }
    });
    box.appendChild(div);
  }
}

// ---------------------------------------------------------------------------
// Result panel
// ---------------------------------------------------------------------------

function renderResultPanel() {
  const panel = $("resultPanel");
  const body = $("resultBody");
  const title = $("resultTitle");
  const sol = state.solution;
  if (!sol) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  if (!sol.feasible) {
    title.textContent = "⚠️ 无解 — 极小无解架次集合";
    let html = `<div class="kv bad"><span>状态</span><b>${sol.status}</b></div>`;
    html += `<div class="hint" style="margin-top:6px">下列每个集合都是包含关系极小的：删去其中任意一架次，其余架次即可消解。互斥时差 Δ = 后者起飞 − 前者起飞（秒）。</div>`;
    for (const core of sol.cores) {
      html += `<div class="core-box">
        <div class="ids">${core.sorties.map(escapeHtml).join("、")}</div>`;
      for (const m of core.mutex_offsets) {
        const iv = m.forbidden_offsets.map(([a, b]) => (a === b ? `${a}` : `[${a}, ${b}]`)).join("，");
        html += `<div class="mutex-line">${escapeHtml(m.sortie_a)} ↔ ${escapeHtml(m.sortie_b)}：禁止 Δ ∈ {${iv}} 秒</div>`;
      }
      html += `</div>`;
    }
    if (sol.truncated) html += `<div class="hint">（无解核可能未列全，受求解时限/数量上限影响）</div>`;
    body.innerHTML = html;
  } else {
    title.textContent = "✅ 无冲突方案";
    const seq = Object.keys(sol.offsets).sort().map((k) => `${escapeHtml(k)}=${sol.offsets[k]}`).join("，");
    body.innerHTML = `
      <div class="kv ok"><span>状态</span><b>${sol.status}</b></div>
      <div class="kv"><span>加权总延误 Σw·d</span><b>${sol.weighted_delay}</b></div>
      <div class="kv"><span>剩余冲突</span><b>${sol.solved_conflicts.length}</b></div>
      <div class="kv"><span>耗时</span><b>${sol.elapsed_seconds} s</b></div>
      <div class="conf-line" style="margin-top:8px">起飞时刻（相对最早起飞，秒）：</div>
      <div style="margin-top:2px">${seq}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Canvas: map
// ---------------------------------------------------------------------------

function getCtx(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (canvas._w !== rect.width || canvas._h !== rect.height) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    canvas._w = rect.width; canvas._h = rect.height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: canvas._w, h: canvas._h };
}

function worldBounds() {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const s of state.sorties) {
    for (const p of s.waypoints) {
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
  }
  if (!isFinite(minX)) return { minX: -100, maxX: 100, minY: -100, maxY: 100 };
  const pad = Math.max(20, 0.08 * Math.max(maxX - minX, maxY - minY, 1));
  return { minX: minX - pad, maxX: maxX + pad, minY: minY - pad, maxY: maxY + pad };
}

function projectors(w, h) {
  const b = worldBounds();
  const sx = w / (b.maxX - b.minX);
  const sy = h / (b.maxY - b.minY);
  const s = Math.min(sx, sy) * 0.92;
  const ox = (w - (b.maxX - b.minX) * s) / 2;
  const oy = (h - (b.maxY - b.minY) * s) / 2;
  return {
    xy: (x, y) => [ox + (x - b.minX) * s, h - (oy + (y - b.minY) * s)],
    scale: s,
  };
}

function drawGrid(ctx, w, h, proj) {
  ctx.strokeStyle = "#1c2740";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#5a6b8f";
  ctx.font = "10px monospace";
  const b = worldBounds();
  const step = niceStep((b.maxX - b.minX) / 8);
  for (let gx = Math.ceil(b.minX / step) * step; gx <= b.maxX; gx += step) {
    const [px] = proj.xy(gx, b.minY);
    ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke();
  }
  for (let gy = Math.ceil(b.minY / step) * step; gy <= b.maxY; gy += step) {
    const [, py] = proj.xy(b.minX, gy);
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(w, py); ctx.stroke();
  }
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / p;
  if (n < 1.5) return p;
  if (n < 3.5) return 2 * p;
  if (n < 7.5) return 5 * p;
  return 10 * p;
}

function drawTrack(ctx, proj, s, takeoff, dashed, time) {
  const c = colorFor(s.id);
  ctx.save();
  ctx.strokeStyle = c;
  ctx.lineWidth = 2;
  ctx.setLineDash(dashed ? [6, 5] : []);
  ctx.globalAlpha = dashed ? 0.5 : 1;
  ctx.beginPath();
  s.waypoints.forEach((p, i) => {
    const [px, py] = proj.xy(p.x, p.y);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.stroke();
  // waypoint dots
  ctx.setLineDash([]);
  for (const p of s.waypoints) {
    const [px, py] = proj.xy(p.x, p.y);
    ctx.beginPath(); ctx.arc(px, py, 2.4, 0, Math.PI * 2); ctx.fillStyle = c; ctx.fill();
  }
  // label at first waypoint
  const p0 = s.waypoints[0];
  const [lx, ly] = proj.xy(p0.x, p0.y);
  ctx.fillStyle = c;
  ctx.font = "11px sans-serif";
  ctx.fillText(`${s.id}${takeoff ? " +" + takeoff + "s" : ""}`, lx + 4, ly - 5);
  // moving aircraft marker
  const pos = positionAt(s, takeoff, time);
  if (pos) {
    const [mx, my] = proj.xy(pos.x, pos.y);
    ctx.beginPath(); ctx.arc(mx, my, dashed ? 5 : 6, 0, Math.PI * 2);
    ctx.fillStyle = dashed ? "rgba(255,255,255,.35)" : c;
    ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
  }
  ctx.restore();
}

function drawConflictMarkers(ctx, proj, conflicts) {
  if (!$("showConflicts").checked) return;
  for (const cf of conflicts) {
    // witness positions are absolute (x,y,z); a_pos/b_pos at witness_time
    const [px, py] = proj.xy(cf.a_pos[0], cf.a_pos[1]);
    ctx.save();
    ctx.strokeStyle = "#ff5d6c";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(px, py, 9, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(px - 13, py); ctx.lineTo(px + 13, py);
    ctx.moveTo(px, py - 13); ctx.lineTo(px, py + 13);
    ctx.stroke();
    ctx.restore();
  }
}

function drawSafetyRadius(ctx, proj, conflicts, R) {
  if (!$("showConflicts").checked) return;
  ctx.save();
  ctx.strokeStyle = "rgba(255,93,108,.55)";
  ctx.setLineDash([3, 3]);
  for (const cf of conflicts) {
    const [px, py] = proj.xy(cf.a_pos[0], cf.a_pos[1]);
    ctx.beginPath();
    ctx.arc(px, py, Math.max(2, R * proj.scale), 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

function renderMap() {
  const canvas = $("mapCanvas");
  const { ctx, w, h } = getCtx(canvas);
  ctx.clearRect(0, 0, w, h);
  if (!state.sorties.length) return;
  const proj = projectors(w, h);
  drawGrid(ctx, w, h, proj);

  const showE = $("showEarliest").checked;
  const showS = $("showSolved").checked;
  const solvedOff = state.solution && state.solution.feasible ? state.solution.offsets : null;

  for (const s of state.sorties) {
    if (showE) drawTrack(ctx, proj, s, 0, true, state.time);
  }
  for (const s of state.sorties) {
    if (showS && solvedOff) drawTrack(ctx, proj, s, solvedOff[s.id] || 0, false, state.time);
  }
  drawSafetyRadius(ctx, proj, state.earliestConflicts, state.scenario.safety_radius);
  drawConflictMarkers(ctx, proj, state.earliestConflicts);
}

// ---------------------------------------------------------------------------
// Canvas: timeline
// ---------------------------------------------------------------------------

function timelineGeometry(w, h) {
  const left = 92, right = 16, top = 16, rowH = (h - top - 12) / Math.max(1, state.sorties.length);
  return { left, right, top, rowH, plotW: w - left - right };
}

function tToX(t, g) {
  return g.left + (t / state.maxTime) * g.plotW;
}

function renderTimeline() {
  const canvas = $("timelineCanvas");
  const { ctx, w, h } = getCtx(canvas);
  ctx.clearRect(0, 0, w, h);
  if (!state.sorties.length) return;
  const g = timelineGeometry(w, h);
  const solvedOff = state.solution && state.solution.feasible ? state.solution.offsets : null;

  // grid + axis
  ctx.strokeStyle = "#243049"; ctx.fillStyle = "#8092b5"; ctx.font = "10px monospace";
  const step = niceStep(state.maxTime / 10);
  for (let t = 0; t <= state.maxTime + 1e-9; t += step) {
    const x = tToX(t, g);
    ctx.beginPath(); ctx.moveTo(x, g.top - 4); ctx.lineTo(x, h - 12); ctx.stroke();
    ctx.fillText(`${t}`, x - 6, h - 1);
  }

  state.sorties.forEach((s, i) => {
    const y = g.top + i * g.rowH;
    const barH = Math.min(20, g.rowH - 8);
    const yc = y + g.rowH / 2;
    ctx.fillStyle = colorFor(s.id);
    ctx.font = "11px sans-serif";
    ctx.fillText(s.id, 8, yc + 4);

    // earliest bar (dashed outline)
    if ($("showEarliest").checked) {
      drawBar(ctx, tToX(0, g), yc - barH / 2, tToX(s.flight_end, g) - tToX(0, g), barH,
              colorFor(s.id), true, 0.45);
    }
    // solved bar
    if (solvedOff && $("showSolved").checked) {
      const t0 = solvedOff[s.id] || 0;
      drawBar(ctx, tToX(t0, g), yc - barH / 2,
              tToX(t0 + s.flight_end, g) - tToX(t0, g), barH,
              colorFor(s.id), false, 0.92);
    }

    // conflict ticks on earliest schedule
    if ($("showConflicts").checked) {
      for (const cf of state.earliestConflicts) {
        if (cf.sortie_a !== s.id && cf.sortie_b !== s.id) continue;
        const x1 = tToX(cf.time_start, g), x2 = tToX(cf.time_end, g);
        ctx.fillStyle = "rgba(255,93,108,.85)";
        ctx.fillRect(Math.min(x1, x2), yc - barH / 2 - 3, Math.max(2, Math.abs(x2 - x1) + 2), 3);
      }
    }
  });

  // playhead
  const px = tToX(state.time, g);
  ctx.strokeStyle = "#4da3ff"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(px, g.top - 6); ctx.lineTo(px, h - 12); ctx.stroke();
}

function drawBar(ctx, x, y, w, h, color, dashed, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  if (dashed) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
    ctx.strokeRect(x, y, w, h);
  } else {
    ctx.fillStyle = color;
    roundRect(ctx, x, y, w, h, 4); ctx.fill();
  }
  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// ---------------------------------------------------------------------------
// Canvas: altitude profile
// ---------------------------------------------------------------------------

function renderProfile() {
  const canvas = $("profileCanvas");
  const { ctx, w, h } = getCtx(canvas);
  ctx.clearRect(0, 0, w, h);
  if (!state.sorties.length) return;
  const g = timelineGeometry(w, h);
  let minZ = 0, maxZ = 1;
  for (const s of state.sorties) for (const p of s.waypoints) maxZ = Math.max(maxZ, p.z);
  const zy = (z) => h - 14 - (z / maxZ) * (h - 28);

  ctx.strokeStyle = "#243049"; ctx.fillStyle = "#8092b5"; ctx.font = "10px monospace";
  for (let z = 0; z <= maxZ + 1e-9; z += niceStep(maxZ / 4)) {
    const y = zy(z);
    ctx.beginPath(); ctx.moveTo(g.left, y); ctx.lineTo(w - g.right, y); ctx.stroke();
    ctx.fillText(`${z}m`, 6, y + 3);
  }

  const solvedOff = state.solution && state.solution.feasible ? state.solution.offsets : null;
  for (const s of state.sorties) {
    const c = colorFor(s.id);
    if ($("showEarliest").checked) {
      drawProfileLine(ctx, s, 0, g, c, zy, true);
    }
    if (solvedOff && $("showSolved").checked) {
      drawProfileLine(ctx, s, solvedOff[s.id] || 0, g, c, zy, false);
    }
  }
}

function drawProfileLine(ctx, s, takeoff, g, c, zy, dashed) {
  ctx.save();
  ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.setLineDash(dashed ? [5, 4] : []);
  ctx.globalAlpha = dashed ? 0.45 : 1;
  ctx.beginPath();
  s.waypoints.forEach((p, i) => {
    const x = tToX(takeoff + p.t, g);
    if (i === 0) ctx.moveTo(x, zy(p.z)); else ctx.lineTo(x, zy(p.z));
  });
  ctx.stroke();
  const pos = positionAt(s, takeoff, state.time);
  if (pos) {
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(tToX(state.time, g), zy(pos.z), 4, 0, Math.PI * 2);
    ctx.fillStyle = c; ctx.fill();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// Legend / render orchestration
// ---------------------------------------------------------------------------

function renderLegend() {
  const box = $("mapLegend");
  box.innerHTML = "";
  for (const s of state.sorties) {
    const span = document.createElement("span");
    span.className = "lg";
    span.innerHTML = `<span class="swatch" style="background:${colorFor(s.id)}"></span>${escapeHtml(s.id)}`;
    box.appendChild(span);
  }
  const cf = document.createElement("span");
  cf.className = "lg";
  cf.innerHTML = `<span style="color:#ff5d6c">✕</span> 冲突见证点（最早起飞）`;
  box.appendChild(cf);
}

function renderAll() {
  if (!state.scenario) return;
  updateMaxTime();
  renderLegend();
  renderMap();
  renderTimeline();
  renderProfile();
  renderSortiePanel();
  renderResultPanel();
}

function escapeHtml(t) {
  return String(t).replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

$("fileInput").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    const text = await f.text();
    const payload = JSON.parse(text);
    await importScenario(payload);
    toast(`已导入 ${payload.sorties?.length ?? 0} 个架次`);
  } catch (err) {
    toast("导入失败：" + err.message, 4200);
  }
  e.target.value = "";
});

$("loadExampleBtn").addEventListener("click", async () => {
  try {
    const res = await fetch("/app/examples/crossroads.json");
    const payload = await res.json();
    await importScenario(payload);
    toast("已载入示例「十字路口」");
  } catch (err) {
    toast("示例载入失败：" + err.message);
  }
});

$("newSolveBtn").addEventListener("click", () => solve());
$("savePlanBtn").addEventListener("click", async () => {
  const name = prompt("为当前方案命名：", `方案 #${state.scenarioId}`);
  if (!name) return;
  try {
    await api(`/api/scenarios/${state.scenarioId}/save`, {
      method: "POST", body: JSON.stringify({ name }),
    });
    toast("方案已保存（含输入快照）");
    await fetchHistory();
  } catch (err) { toast("保存失败：" + err.message); }
});

$("closeResult").addEventListener("click", () => $("resultPanel").classList.add("hidden"));

$("timeScrub").addEventListener("input", (e) => {
  state.time = (parseFloat(e.target.value) / 100) * state.maxTime;
  $("timeLabel").textContent = `t = ${state.time.toFixed(1)} s`;
  renderMap(); renderTimeline(); renderProfile();
});

["showEarliest", "showSolved", "showConflicts"].forEach((id) => {
  $(id).addEventListener("change", () => { renderMap(); renderTimeline(); renderProfile(); });
});

["safetyRadius", "safetyHeight"].forEach((id) => {
  $(id).addEventListener("change", () => {
    if (state.scenarioId) solve(collectLocks());
  });
});
window.addEventListener("resize", () => { if (state.scenario) renderAll(); });

// init
(async function init() {
  try {
    await fetchHistory();
  } catch (e) { /* empty DB */ }
})();

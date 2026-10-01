# 无人机航迹冲突消解台（Drone Conflict Console）

一个**本地运行**的 Web 应用：导入以 **米制三维坐标** 与 **相对起飞秒数** 表示的
分段直线航迹，系统在连续时间上检测冲突，并用 OR-Tools 求出让所有架次
**仅通过整体平移起飞时刻**即可无冲突的整数秒方案；无解时给出包含关系极小的
无解架次集合与互斥时差。支持锁定部分起飞时刻重算、页面对照“最早起飞”与
“求解后”两套航迹，方案可保存并随时回看（保留输入快照）；可从可行方案创建
**执行批次**，冻结航迹、安全阈值与计划偏移，上报实际起飞偏差并在连续时间上
重算执行预警（页面与 HTTP API 使用同一批次数据）。

---

## 功能一览

- **导入 JSON**：每个架次含固定三维分段直线航迹、整数秒起飞窗口 `[0, window_hi]`、
  正整数优先级权重 `weight`；只允许整体平移起飞时刻。
- **连续冲突检测**：在飞行时间重叠区间内逐时刻检查（含所有航段端点），
  当且仅当
  - 水平距离 `< 安全半径 R`，**且**
  - 垂直距离 `< 安全高度 H`

  时判定为冲突。水平/垂直是“与”的关系（相当于竖直安全圆柱）。
- **精确整数冲突模型**：对每对架次、每对航段，用解析几何
  （垂直条带裁剪 + 水平椭圆柱相交）求出会造成冲突的**相对起飞时差整数集合**，
  得到每对架次的“互斥时差区间”。
- **CP-SAT 求解（OR-Tools）**：选择每个架次的整数秒起飞偏移 `x_i ∈ [0, window_hi]`，
  避开所有互斥时差。
  - 第一目标：**加权总延误** `Σ w_i · x_i`（相对于各自最早起飞时刻）最小；
  - 第二目标：在最优目标值相同的方案中，取**按架次 ID 排序的起飞时刻序列字典序最小**，
    结果唯一、可复现。
- **无解诊断**：无解时列出若干**包含关系极小**的无解架次集合（删去其中任意一架次，
  其余架次即可消解），并给出每对架次的**互斥时差**（后者起飞 − 前者起飞，秒）。
- **锁定重算**：在页面勾选锁定并填写某些架次的整数秒偏移，重新求解其余架次。
- **可视化对照**：
  - 水平航迹图（最早起飞虚线 / 求解后实线，冲突见证点用红色 ✕ 与安全圆标出）；
  - 甘特式时间轴（红色段为最早起飞时的冲突时间）；
  - 高度剖面图；
  - 时间游标可拖动，观察各机实时位置。
- **方案留存**：保存方案时连同**输入 JSON 快照**、安全参数与求解结果一并存入
  本地 SQLite，之后可随时打开回看或删除。
- **执行偏差预警**：从已求解出的可行方案创建执行批次；批次独立冻结输入航迹、
  安全半径/高度和计划偏移，原方案随后重算或删除都不会改变批次。各架次按修订号
  上报非负整数秒的实际起飞偏移，页面与 API 共用同一份批次数据；按当前秒 `t`
  将已上报架次按实际时刻、未逾期架次按计划时刻纳入连续冲突预测，逾期架次排除并
  明确标记“预测不完整”。

---

## 目录结构

```
drone-conflict-console/
├── app/
│   ├── main.py          # FastAPI 路由
│   ├── model.py         # 航迹/航段数据模型
│   ├── geometry.py      # 连续冲突检测 + 互斥时差解析几何
│   ├── solver.py        # OR-Tools CP-SAT 建模、字典序择优、无解核枚举
│   ├── execution.py     # 执行批次：实际/计划/逾期归类与预警结果
│   ├── schemas.py       # 导入/求解/批次请求的校验
│   ├── storage.py       # SQLite 持久化（方案、冻结批次和上报）
│   └── static/          # 原生 JS + Canvas 前端（无构建步骤）
├── examples/crossroads.json
├── examples/execution-demo.json
├── tests/               # 几何三实现交叉验证 + 求解器暴力枚举验证
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 一键启动（Docker Compose，推荐）

需要安装 Docker 与 Docker Compose（v2）。

```bash
docker compose up --build
```

启动后用浏览器访问：

- **应用页面**：<http://localhost:8000/>（求解、保存、批次、上报和预警都在此页面）
- API 健康检查：<http://localhost:8000/api/health>
- OpenAPI 文档（可选）：<http://localhost:8000/docs>

数据保存在 Docker 命名卷 `dcc-data`（容器内 `/data/console.db`），
停止/重建容器不会丢失已保存方案。

停止：

```bash
docker compose down          # 保留数据
docker compose down -v       # 同时删除已保存数据
```

---

## 本地直接运行（不使用 Docker）

需要 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 127.0.0.1 --port 8000
# 或： python -m uvicorn app.main:app --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。
数据库默认写在 `./data/console.db`，可用环境变量 `DCC_DB_PATH` 改路径。

---

## 使用方式

1. 打开页面，点右上角 **导入航迹 JSON**（或点 **载入示例** 体验
   `examples/crossroads.json`）。
2. 在左侧调整 **安全半径 / 安全高度**（修改后会自动重新求解）。
3. 右侧查看：
   - 水平航迹、时间轴、高度剖面；拖动顶部时间游标看实时位置；
   - 底部结果面板显示是否有解、加权总延误、各架次起飞偏移；
   - 无解时面板列出极小无解架次集合与互斥时差。
4. 如需固定某些架次：在 **架次与锁定** 中勾选“锁定”，填整数秒偏移，
   再点顶部 **重算（应用锁定）**。
5. 点 **保存当前方案** 命名存档；之后在左下 **已保存方案** 中点击即可
   连同输入快照重新载入，🗑 可删除。
6. 得到可行方案后点 **从可行方案创建批次**。批次会冻结航迹、安全阈值
   `R/H` 和计划偏移；再对原方案修改安全参数并重算，批次仍保持不变。
7. 在 **执行偏差预警** 卡片选择架次，填写非负整数“实际起飞偏移”和修订号，
   首报修订号为 `1`；点 **上报**。纠错时使用更大的修订号。
8. 填写非负当前秒 `t` 后点 **重算预警**：已上报架次用实际偏移，未上报且计划
   起飞 `>= t` 的架次用计划偏移，未上报且计划起飞 `< t` 的架次标记为逾期并
   排除，结果会标明预测是否完整。

---

## 输入 JSON 格式

顶层为一个对象（也可以先用 `examples/crossroads.json` 对照）：

```json
{
  "safety_radius": 30,
  "safety_height": 10,
  "sorties": [
    {
      "id": "UAV-A",
      "weight": 3,
      "window_hi": 20,
      "waypoints": [
        {"x": 0,   "y": 0,  "z": 20, "t": 0},
        {"x": 120, "y": 0,  "z": 20, "t": 12},
        {"x": 240, "y": 0,  "z": 20, "t": 24}
      ]
    }
  ]
}
```

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `safety_radius` | 水平安全半径（米） | 正数 |
| `safety_height` | 垂直安全高度（米） | 正数 |
| `sorties[].id` | 架次唯一标识 | 非空、不重复 |
| `sorties[].weight` | 优先级权重 | **正整数**，越大延误代价越高 |
| `sorties[].window_hi` | 最晚相对起飞时刻（秒） | 非负整数；起飞窗口为 `[0, window_hi]` |
| `waypoints[].x/y/z` | 米制三维坐标 | 数值 |
| `waypoints[].t` | 相对本架次起飞的航点时刻（秒） | **非负整数，严格递增**，至少 2 个航点 |

说明：相邻航点之间以匀速直线运动相连；导入时每个架次的最早起飞时刻统一记为 0，
求解得到的 `offset` 即“相对最早起飞的整数秒平移量”，最终实际起飞时刻 = 该偏移。

导入接口：`POST /api/import`（页面导入时安全参数以 JSON 内的为准；
后续可在 `/api/scenarios/{id}/solve` 请求体中临时覆盖）。

---

## 求解与无解说说明（建模要点）

- **互斥时差**：对架次 A、B，若 `x_B − x_A` 落入某整数区间 `[lo, hi]` 就会冲突，
  则求解器施加析取约束 `x_B − x_A ≥ hi+1 ∨ x_B − x_A ≤ lo−1`。
- 这些区间来自**连续时间解析几何**（而非秒级采样），保证不漏检；
  代码中的检测对端点与擦边情形按安全侧（落在边界也判冲突）处理。
- **无解核（MUS）**：每个架次带一个“参与”布尔并作为 CP-SAT 假设；
  返回的不可满足核心天然是包含关系极小的。再通过“强制某架次退出”分支，
  枚举出更多极小集合，并附带每对架次的互斥时差。
- 目标采用两阶段优化：先最小化加权总延误并固定最优值，
  再按架次 ID 逐个最小化偏移，得到唯一的字典序最优时刻序列。

---

## API 摘要

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/api/import` | 导入航迹 JSON，返回架次/互斥时差/最早起飞冲突 |
| `POST` | `/api/scenarios/{id}/solve` | 按（可覆盖的）安全参数与锁定重算 |
| `POST` | `/api/scenarios/{id}/save` | 命名保存当前方案（含输入快照） |
| `POST` | `/api/scenarios/{id}/batches` | 从当前已求解的可行方案创建冻结执行批次 |
| `GET` | `/api/scenarios` | 已保存方案列表 |
| `GET` | `/api/scenarios/{id}` | 回看某个方案（输入快照 + 求解结果） |
| `DELETE` | `/api/scenarios/{id}` | 删除方案（冻结批次仍保留） |
| `GET` | `/api/batches` | 批次列表（页面和 API 共用） |
| `GET` | `/api/batches/{id}` | 查看批次；带 `?t=...` 时同时计算预警 |
| `POST` | `/api/batches/{id}/reports` | 上报实际起飞偏移和修订号 |
| `GET` | `/api/batches/{id}/warning?t=...` | 计算当前秒预警 |
| `DELETE` | `/api/batches/{id}` | 删除批次及其上报记录 |
| `GET` | `/api/health` | 健康检查 |

上报请求体：

```json
{
  "reports": [
    {"sortie_id": "UAV-B", "actual_offset": 1, "revision": 1}
  ]
}
```

修订规则：首报修订号必须为正整数，首版用 `1`；同一架次后续修订号必须严格
增大。同修订号、同实际偏移的重试幂等；同修订号异值或更小修订号返回 `409`。
实际偏移必须是非负整数秒。

预警规则（`GET /api/batches/{id}/warning?t=1`）：

- `t` 必须是非负整数秒；若 `t` 早于任一已上报实际起飞时刻，返回 `400`；
- 已上报架次始终按实际起飞偏移纳入预测，并返回相对计划的秒数
  `actual_offset - planned_offset`；
- 未上报且 `planned_offset >= t` 的架次按计划起飞纳入预测；
- 未上报且 `planned_offset < t` 的架次标记为 `overdue` 并排除，`incomplete=true`；
- 冲突仍沿用连续时间判定，只返回开始时刻不早于 `t` 的区间和首个冲突时刻。

---

## 可复现的端到端 HTTP 示例

以下命令在仓库根目录 `drone-conflict-console/` 下执行，服务已通过
`docker compose up --build` 启动。示例文件为
`examples/execution-demo.json`：两架同高度 20 m 的飞机在点 `(50,0)` 十字交叉，
安全半径 10 m；最优计划为 `UAV-A=0s`、`UAV-B=2s`，加权延误 `2`。下面的字面
响应假设使用空数据库（全新命名卷），实际 ID 以命令中的 `$SID/$BID` 为准。

### 1. 导入并求解，保存方案，创建冻结批次

```bash
SID=$(curl -s -X POST http://localhost:8000/api/import \
  -H 'Content-Type: application/json' \
  --data @examples/execution-demo.json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["scenario_id"])')

curl -s -X POST "http://localhost:8000/api/scenarios/${SID}/solve" \
  -H 'Content-Type: application/json' \
  -d '{"time_limit": 20}' \
| python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["solution"], ensure_ascii=False, indent=2))'

curl -s -X POST "http://localhost:8000/api/scenarios/${SID}/save" \
  -H 'Content-Type: application/json' \
  -d '{"name":"execution-demo"}'

BID=$(curl -s -X POST "http://localhost:8000/api/scenarios/${SID}/batches" \
  -H 'Content-Type: application/json' \
  -d '{"name":"首批执行"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["batch_id"])')

echo "SID=$SID BID=$BID"
```

求解步骤中稳定的关键字段为（`elapsed_seconds` 随机器变化）：

```json
{
  "feasible": true,
  "status": "optimal",
  "weighted_delay": 2,
  "offsets": {"UAV-A": 0, "UAV-B": 2},
  "locked": {},
  "solved_conflicts": [],
  "cores": [],
  "truncated": false
}
```

### 2. 先只上报 UAV-B：t=1 时 UAV-A 逾期，预测不完整

```bash
curl -s -X POST "http://localhost:8000/api/batches/${BID}/reports" \
  -H 'Content-Type: application/json' \
  -d '{"reports":[{"sortie_id":"UAV-B","actual_offset":1,"revision":1}]}'

curl -s "http://localhost:8000/api/batches/${BID}/warning?t=1" \
| python3 -m json.tool
```

响应中的关键结果：

```json
{
  "batch_id": 1,
  "warning": {
    "t": 1,
    "incomplete": true,
    "complete": false,
    "conflicts": [],
    "conflict_count": 0,
    "first_conflict_time": null,
    "sorties": [
      {"sortie_id": "UAV-A", "status": "overdue", "planned_offset": 0,
       "actual_offset": null, "revision": null, "deviation_seconds": null,
       "used_offset": null},
      {"sortie_id": "UAV-B", "status": "reported", "planned_offset": 2,
       "actual_offset": 1, "revision": 1, "deviation_seconds": -1,
       "used_offset": 1}
    ],
    "reported_actual_offsets": {"UAV-B": 1},
    "deviations": {"UAV-B": -1}
  }
}
```

### 3. 上报 UAV-A 后重算：连续时间预警给出冲突区间

```bash
curl -s -X POST "http://localhost:8000/api/batches/${BID}/reports" \
  -H 'Content-Type: application/json' \
  -d '{"reports":[{"sortie_id":"UAV-A","actual_offset":0,"revision":1}]}'

curl -s "http://localhost:8000/api/batches/${BID}/warning?t=1" \
| python3 -m json.tool
```

关键响应（坐标和时间均由连续匀速航迹解析得到）：

```json
{
  "t": 1,
  "incomplete": false,
  "complete": true,
  "conflict_count": 1,
  "first_conflict_time": 5.0,
  "conflicts": [
    {
      "sortie_a": "UAV-A",
      "sortie_b": "UAV-B",
      "time_start": 5.0,
      "time_end": 6.0,
      "witness_time": 5.5,
      "a_pos": [55.0, 0.0, 20.0],
      "b_pos": [50.0, -5.0, 20.0],
      "horizontal_distance": 7.071068,
      "vertical_distance": 0.0
    }
  ],
  "deviations": {"UAV-A": 0, "UAV-B": -1}
}
```

### 4. 幂等、冲突和更高修订号纠错

同号同值重试返回 `idempotent`，不改变状态：

```bash
curl -s -X POST "http://localhost:8000/api/batches/${BID}/reports" \
  -H 'Content-Type: application/json' \
  -d '{"reports":[{"sortie_id":"UAV-B","actual_offset":1,"revision":1}]}'
```

```json
{"batch_id":1,"accepted":true,"outcomes":{"UAV-B":"idempotent"}}
```

同号异值返回 `409`：

```bash
curl -i -X POST "http://localhost:8000/api/batches/${BID}/reports" \
  -H 'Content-Type: application/json' \
  -d '{"reports":[{"sortie_id":"UAV-B","actual_offset":2,"revision":1}]}'
```

```http
HTTP/1.1 409 Conflict
```

```json
{
  "detail": {
    "error": "revision_conflict",
    "sortie_id": "UAV-B",
    "submitted_revision": 1,
    "stored_revision": 1
  }
}
```

用更大的修订号 `2` 纠错为计划值 2 秒，再重算：冲突消失。

```bash
curl -s -X POST "http://localhost:8000/api/batches/${BID}/reports" \
  -H 'Content-Type: application/json' \
  -d '{"reports":[{"sortie_id":"UAV-B","actual_offset":2,"revision":2}]}'

curl -s "http://localhost:8000/api/batches/${BID}/warning?t=2"
```

```json
{
  "batch_id": 1,
  "warning": {
    "t": 2,
    "incomplete": false,
    "complete": true,
    "conflicts": [],
    "conflict_count": 0,
    "first_conflict_time": null,
    "reported_actual_offsets": {"UAV-A": 0, "UAV-B": 2},
    "deviations": {"UAV-A": 0, "UAV-B": 0}
  }
}
```

此时旧修订号会返回 `409`，而 `t=1` 早于 UAV-B 已上报实际起飞时刻 2，返回
`400`：

```json
{"detail":"current t=1 is earlier than reported actual take-off 2 for UAV-B"}
```

### 5. 验证批次冻结：重算原方案不影响批次

把原方案安全半径临时改成 100 m 重算，求解器会得到不同计划；随后重新读取同一
批次，其 `safety_radius` 仍是 10、`planned_offsets.UAV-B` 仍是 2，批次上报和
预警结果也不变：

```bash
curl -s -X POST "http://localhost:8000/api/scenarios/${SID}/solve" \
  -H 'Content-Type: application/json' \
  -d '{"safety_radius":100,"safety_height":10,"time_limit":20}' \
| python3 -c 'import json,sys; print(json.load(sys.stdin)["solution"]["offsets"])'

curl -s "http://localhost:8000/api/batches/${BID}" \
| python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["safety_radius"], d["planned_offsets"], d["reports"]["UAV-B"]["revision"])'
```

输出分别为：

```text
{'UAV-A': 0, 'UAV-B': 11}
10.0 {'UAV-A': 0, 'UAV-B': 2} 2
```

重新 GET 同一预警 URL 会由冻结快照和已持久化上报重新计算，结果一致。

---

## 测试

测试不依赖容器，在本地虚拟环境中运行：

```bash
pip install -r requirements.txt
python tests/test_geometry_crossval.py
python tests/test_solver_bruteforce.py 2026
PYTHONPATH=. python -m unittest tests.test_execution_batches tests.test_scan_schedule_clip -v
```

- `test_geometry_crossval.py` 用三套相互独立的实现（多边形/椭圆柱解析、
  连续时间区间剖分、密采样神谕）对上千个随机航段对与擦边案例做一致性校验；
- `test_solver_bruteforce.py` 在小窗口下穷举所有起飞组合，验证最优目标、
  字典序择优、锁定约束以及无解核的可行性与包含极小性；
- `test_execution_batches.py` 覆盖冻结快照、实际/计划/逾期归类、同号幂等、
  同号异值/旧号冲突、并发上报最高修订胜出以及批次重载一致性。

---

## 备注与边界

- 冲突为严格不等式（`水平 < R` 且 `垂直 < H`）；实现采用极小容差按安全侧处理，
  恰好落在边界的情形会被当作冲突，宁可保守。
- 起飞时刻与航点时刻均为整数秒；航迹在两段之间为连续匀速直线，
  冲突检测本身在连续时间上进行。
- 求解时限可在页面或请求中设置（默认 20 秒）。正常规模下加权延误优化通常
  很快即可得到 `optimal`；超时会返回当前最优 `feasible` 并在状态中标明。

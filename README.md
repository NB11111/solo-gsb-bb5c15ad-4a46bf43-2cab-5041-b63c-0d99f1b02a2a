# 直流配电树压降校核 API

基于 FastAPI 的直流配电树校核服务：输入以 `source` 为根的配电树（电源电压、线段电阻与额定电流、各节点恒定负载、最低允许电压），自叶向根累计每段承载的全部下游电流，再自根向叶计算压降与节点电压，返回各段电流、各节点电压以及超额定电流 / 欠压位置；并可评估指定节点可追加的最大恒定负载电流（`/capacity`），根据若干线段的实测电流定位唯一未登记正恒定负载的候选节点（`/unregistered-load`），或校核至多 K 条负载同时升至峰值时每条线段的最大承载电流与每个节点的最低电压（`/peak-risk`）。

## 快速启动（单条命令）

```bash
docker compose up --build
```

容器启动时会自动安装依赖（`pip install -r requirements.txt`）并启动服务，启动后访问 `http://localhost:8000`（端口可由 `PORT` 环境变量修改，见下表）。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 宿主机映射端口，如 `PORT=9000 docker compose up --build` 后访问 `http://localhost:9000` |

## API 地址

- 服务地址：`http://localhost:8000`
- 交互式文档（Swagger UI）：`http://localhost:8000/docs`
- 健康检查：`GET http://localhost:8000/health`
- 校核接口：`POST http://localhost:8000/evaluate`
- 增容评估接口：`POST http://localhost:8000/capacity`
- 未登记负载定位接口：`POST http://localhost:8000/unregistered-load`
- 峰值并发风险接口：`POST http://localhost:8000/peak-risk`

## 请求格式

`POST /evaluate`，Content-Type: `application/json`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `source_voltage` | number | > 0 | 电源电压 (V) |
| `min_voltage` | number | ≥ 0 | 全局最低允许电压 (V) |
| `segments` | array | 至少 1 条 | 线段列表，须构成以 `source` 为根的连通树 |
| `segments[].id` | string | 可选，全局唯一 | 线段标识，缺省自动生成 `seg-<序号>` |
| `segments[].parent` | string | 必填 | 父节点名称，根节点固定为 `source` |
| `segments[].child` | string | 必填 | 子节点名称 |
| `segments[].resistance` | number | ≥ 0 | 线段电阻 (Ω) |
| `segments[].rated_current` | number | > 0 | 线段额定电流 (A) |
| `loads` | array | 可空 | 各节点恒定负载；同一节点多条负载自动累加 |
| `loads[].node` | string | 必填 | 节点名称（须存在于树中，可为 `source`） |
| `loads[].current` | number | ≥ 0 | 恒定负载电流 (A) |
| `loads[].min_voltage` | number | 可选，≥ 0 | 该节点最低允许电压，覆盖全局值 |

### 请求示例

```bash
curl -X POST http://localhost:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "source_voltage": 48.0,
    "min_voltage": 40.0,
    "segments": [
      {"id": "s1", "parent": "source", "child": "a", "resistance": 0.1,  "rated_current": 20.0},
      {"id": "s2", "parent": "a",      "child": "b", "resistance": 0.2,  "rated_current": 10.0},
      {"id": "s3", "parent": "a",      "child": "c", "resistance": 0.05, "rated_current": 15.0}
    ],
    "loads": [
      {"node": "a", "current": 2.0},
      {"node": "b", "current": 5.0},
      {"node": "c", "current": 8.0}
    ]
  }'
```

### 响应示例（200）

```json
{
  "ok": true,
  "source_voltage": 48.0,
  "min_voltage": 40.0,
  "total_source_current": 15.0,
  "segments": [
    {"segment_id": "s1", "parent": "source", "child": "a", "current": 15.0, "rated_current": 20.0, "resistance": 0.1, "voltage_drop": 1.5, "over_rated": false},
    {"segment_id": "s2", "parent": "a", "child": "b", "current": 5.0, "rated_current": 10.0, "resistance": 0.2, "voltage_drop": 1.0, "over_rated": false},
    {"segment_id": "s3", "parent": "a", "child": "c", "current": 8.0, "rated_current": 15.0, "resistance": 0.05, "voltage_drop": 0.4, "over_rated": false}
  ],
  "nodes": [
    {"node": "source", "voltage": 48.0, "min_voltage": 40.0, "load_current": 0.0, "under_voltage": false},
    {"node": "a", "voltage": 46.5, "min_voltage": 40.0, "load_current": 2.0, "under_voltage": false},
    {"node": "b", "voltage": 45.5, "min_voltage": 40.0, "load_current": 5.0, "under_voltage": false},
    {"node": "c", "voltage": 46.1, "min_voltage": 40.0, "load_current": 8.0, "under_voltage": false}
  ],
  "over_current_segments": [],
  "under_voltage_nodes": []
}
```

- `segments[].current`：该段承载的全部下游电流（自叶向根累计）。
- `nodes[].voltage`：自根向叶逐段扣除 `电流 × 电阻` 压降后的节点电压。
- `over_current_segments`：超额定电流的线段（含 `excess` 超出量）。
- `under_voltage_nodes`：欠压节点（含 `deficit` 欠压差值）。
- `ok`：无超额定且无欠压时为 `true`。

## 增容评估：POST /capacity

`POST /capacity`，Content-Type: `application/json`。请求沿用 `/evaluate` 的全部参数，并额外指定：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `target_node` | string | 必填 | 目标节点，须为已有的非 `source` 节点 |

计算规则：

- 先按 `/evaluate` 的校核逻辑计算基线；若基线已存在超额定电流或欠压，返回 `400 baseline_violation` 并在 `details` 中列出越限位置；
- 新增电流 ΔI 使目标到 `source` 路径上的线段电流同步增加；
- 任一节点的新增压降 = 它与目标节点两条根路径的公共线段电阻之和 × ΔI（兄弟支路经公共上游线段同样计入）；
- 最大 ΔI 取「路径各段额定电流余量」与「各受影响节点欠压余量 ÷ 公共电阻」的最小值；零公共电阻的节点不受影响；
- 结果向下截断至 6 位小数；恰好触限（电流等于额定、电压等于最低允许电压）视为可行。

### 请求示例

```bash
curl -X POST http://localhost:8000/capacity \
  -H "Content-Type: application/json" \
  -d '{
    "source_voltage": 48.0,
    "min_voltage": 40.0,
    "segments": [
      {"id": "s1", "parent": "source", "child": "a", "resistance": 0.1,  "rated_current": 20.0},
      {"id": "s2", "parent": "a",      "child": "b", "resistance": 0.2,  "rated_current": 10.0},
      {"id": "s3", "parent": "a",      "child": "c", "resistance": 0.05, "rated_current": 15.0}
    ],
    "loads": [
      {"node": "a", "current": 2.0},
      {"node": "b", "current": 5.0},
      {"node": "c", "current": 8.0}
    ],
    "target_node": "b"
  }'
```

### 响应示例（200）

```json
{
  "target_node": "b",
  "max_additional_current": 5.0,
  "limiting_factors": [
    {"type": "segment", "id": "s1"},
    {"type": "segment", "id": "s2"}
  ]
}
```

- `max_additional_current`：目标节点可追加的最大恒定负载电流 (A)，向下截断至 6 位小数。
- `limiting_factors`：达到上限的限制因素；`type=segment` 表示线段额定电流限制，`type=node` 表示节点欠压限制，`id` 为线段 id 或节点名称。多个因素同时触限时全部列出。

## 未登记负载定位：POST /unregistered-load

`POST /unregistered-load`，Content-Type: `application/json`。请求沿用 `/evaluate` 的全部参数（`min_voltage` 参与校验但不影响定位），并额外附加若干线段的实测电流：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `measurements` | array | 可空 | 线段实测电流列表，测点可不覆盖全树 |
| `measurements[].segment_id` | string | 必填，唯一且存在于树中 | 被测线段标识（重复或不存在返回 `400 invalid_input`） |
| `measurements[].current` | number | ≥ 0 且为有限数 | 实测电流 (A)（负数或 NaN/Infinity 返回 `422`） |

判定规则（假定恰有一个非 `source` 节点存在未登记的正恒定负载）：

- 对每个测点计算 **差值 = 实测电流 − 未舍入基线线段电流**；|差值| ≤ 1e-6 A 视为无差异；
- 出现负差（差值 < -1e-6 A，实测低于基线）→ `inconsistent`；
- 显著正差（差值 > 1e-6 A）须在 1e-6 A 内相互一致，否则 → `inconsistent`；
- 无显著正差 → `undetermined`（未登记负载为零、过小或位于未测支路，无法确定）；
- 差值可确定时，未登记负载电流取显著正差的均值（按六位小数返回），并筛选候选节点：显著正差测点须位于候选节点的祖先路径上、零差测点须不在其祖先路径上；未测支路允许多解（返回多个候选节点），无相容节点 → `inconsistent`。

### 请求示例

```bash
curl -X POST http://localhost:8000/unregistered-load \
  -H "Content-Type: application/json" \
  -d '{
    "source_voltage": 48.0,
    "min_voltage": 40.0,
    "segments": [
      {"id": "s1", "parent": "source", "child": "a", "resistance": 0.1,  "rated_current": 20.0},
      {"id": "s2", "parent": "a",      "child": "b", "resistance": 0.2,  "rated_current": 10.0},
      {"id": "s3", "parent": "a",      "child": "c", "resistance": 0.05, "rated_current": 15.0}
    ],
    "loads": [
      {"node": "a", "current": 2.0},
      {"node": "b", "current": 5.0},
      {"node": "c", "current": 8.0}
    ],
    "measurements": [
      {"segment_id": "s1", "current": 18.0},
      {"segment_id": "s3", "current": 8.0}
    ]
  }'
```

### 响应示例（200，located）

```json
{
  "status": "located",
  "load_current": 3.0,
  "candidate_nodes": ["a", "b"],
  "measurements": [
    {"segment_id": "s1", "baseline_current": 15.0, "measured_current": 18.0, "difference": 3.0},
    {"segment_id": "s3", "baseline_current": 8.0, "measured_current": 8.0, "difference": 0.0}
  ],
  "reasons": []
}
```

- `status`：`located`（已定位，返回候选节点与负载电流）、`undetermined`（无显著正差，无法确定）、`inconsistent`（实测与基线矛盾）。
- `load_current`：未登记负载电流估计值 (A)，六位小数；仅 `located` 时给出，其余为 `null`。
- `candidate_nodes`：与全部测点相容的节点（BFS 顺序）；仅 `located` 时非空。上例中 s1 正差 +3A 而 s3 无差异，负载在 s1 下游但不在 c 支路，故候选为 `a`、`b`。
- `measurements`：各测点与未舍入基线的对比（展示值按 6 位小数舍入，判定使用未舍入值）。
- `reasons`：判定为 `inconsistent` 的原因列表，其余情况为空。

## 峰值并发风险：POST /peak-risk

`POST /peak-risk`，Content-Type: `application/json`。请求沿用 `/evaluate` 的配电树输入，每条负载可额外给出峰值电流，并指定同时升峰负载上限：

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `loads[].peak_current` | number | 可选，≥ `current`、≥ 0 且为有限数 | 该负载的峰值电流 (A)，缺省等于 `current`（即不升峰） |
| `max_simultaneous_peaks` | integer | 必填，非负整数 | 最多同时升至峰值的负载条数 K，其余负载保持 `current` |

负载按请求中的输入顺序从 **1** 开始编号；同一节点的多条负载分别计数、各自可独立升峰。

计算规则（对每条线段、每个节点分别独立校核）：

- 每条负载的升峰增量 ΔI = `peak_current − current`；ΔI 为 0 的负载不参与选取；
- **线段最大承载电流**：负载位于该线段下游时，其增流贡献为 ΔI（升峰使路径线段同步增流）；取贡献最大的至多 K 条，其增量之和加基线线段电流即为最坏值；
- **节点最低电压**：负载升峰对被校核节点的附加压降贡献 = R_common × ΔI，R_common 为「负载节点到 `source` 路径」与「被校核节点到 `source` 路径」公共线段的电阻之和（兄弟支路经公共上游线段同样受影响）；取贡献最大的至多 K 条，其附加压降之和从基线节点电压中扣除即为最坏值；
- 贡献相同时按负载输入序号升序选取（序号小者优先）；有贡献的负载不足 K 条时全部选取；
- **不同约束（线段 / 节点）允许对应不同的峰值负载组合**；
- 越限判定沿用 `/evaluate`：线段最坏电流 > 额定电流、节点最坏电压 < 生效的最低允许电压（含节点级 `min_voltage` 覆盖），使用相同的 1e-9 容差，恰好触限不算越限。

### 请求示例

```bash
curl -X POST http://localhost:8000/peak-risk \
  -H "Content-Type: application/json" \
  -d '{
    "source_voltage": 48.0,
    "min_voltage": 40.0,
    "segments": [
      {"id": "s1", "parent": "source", "child": "a", "resistance": 0.1,  "rated_current": 20.0},
      {"id": "s2", "parent": "a",      "child": "b", "resistance": 0.2,  "rated_current": 10.0},
      {"id": "s3", "parent": "a",      "child": "c", "resistance": 0.05, "rated_current": 15.0}
    ],
    "loads": [
      {"node": "a", "current": 2.0, "peak_current": 5.0},
      {"node": "b", "current": 5.0, "peak_current": 9.0},
      {"node": "c", "current": 8.0}
    ],
    "max_simultaneous_peaks": 1
  }'
```

### 响应示例（200）

```json
{
  "ok": true,
  "source_voltage": 48.0,
  "min_voltage": 40.0,
  "max_simultaneous_peaks": 1,
  "segments": [
    {"segment_id": "s1", "parent": "source", "child": "a", "current": 15.0, "worst_current": 19.0, "rated_current": 20.0, "peak_load_indices": [2], "over_rated": false},
    {"segment_id": "s2", "parent": "a", "child": "b", "current": 5.0, "worst_current": 9.0, "rated_current": 10.0, "peak_load_indices": [2], "over_rated": false},
    {"segment_id": "s3", "parent": "a", "child": "c", "current": 8.0, "worst_current": 8.0, "rated_current": 15.0, "peak_load_indices": [], "over_rated": false}
  ],
  "nodes": [
    {"node": "source", "voltage": 48.0, "worst_voltage": 48.0, "min_voltage": 40.0, "load_current": 0.0, "peak_load_indices": [], "under_voltage": false},
    {"node": "a", "voltage": 46.5, "worst_voltage": 46.1, "min_voltage": 40.0, "load_current": 2.0, "peak_load_indices": [2], "under_voltage": false},
    {"node": "b", "voltage": 45.5, "worst_voltage": 44.3, "min_voltage": 40.0, "load_current": 5.0, "peak_load_indices": [2], "under_voltage": false},
    {"node": "c", "voltage": 46.1, "worst_voltage": 45.7, "min_voltage": 40.0, "load_current": 8.0, "peak_load_indices": [2], "under_voltage": false}
  ],
  "over_current_segments": [],
  "under_voltage_nodes": []
}
```

上例 K=1：序号 2（节点 b，Δ4A）是线段 s1（下游 Δ4 vs Δ3）、s2（仅其有增量）以及节点 a / b / c（与 b 的公共上游 s1，附加压降 0.1×4=0.4V；b 自身公共电阻 0.3Ω，1.2V）的最大贡献；s3 下游负载无升峰增量，故 `peak_load_indices` 为空，但节点 c 仍会因 b 支路经公共线段 s1 升峰而被压低电压——两类约束的峰值组合相互独立。

- `segments[].current` / `nodes[].voltage`：基线（全部负载取 `current` 时）的线段电流与节点电压。
- `segments[].worst_current`：至多 K 条负载升峰时该段的**最大承载电流** (A)。
- `nodes[].worst_voltage`：至多 K 条负载升峰时该节点的**最低电压** (V)。
- `peak_load_indices`：达到该约束最坏值的升峰负载序号（1 起，按贡献降序、同贡献按序号升序）；无有贡献负载时为空列表。
- `over_current_segments` / `under_voltage_nodes`：最坏工况下的越限列表，各条目除沿用 `/evaluate` 的字段（线段含 `current` / `excess`，节点含 `voltage` / `deficit`，数值均为越限工况值）外，另带 `peak_load_indices` 指出导致越限的升峰负载。
- `ok`：最坏工况下无超额定且无欠压时为 `true`。

## 错误说明

所有错误均返回统一结构：

```json
{
  "error": {
    "code": "validation_error",
    "message": "请求参数校验失败",
    "details": [{"field": "source_voltage", "message": "电源电压必须为正数（> 0）"}]
  }
}
```

| 状态码 | code | 触发场景 |
| --- | --- | --- |
| `422` | `validation_error` | 非正电源电压、负电阻、负负载电流、非正额定电流、`peak_current` 小于 `current` / 为负 / NaN/Infinity、`max_simultaneous_peaks` 缺失或不是非负整数、实测电流为负或不是有限数（NaN/Infinity）、字段缺失或类型错误等 |
| `400` | `invalid_input` | 无效树（不连通、存在环路、节点多父节点、`source` 作为子节点、线段 id 重复）、负载引用不存在的节点、`target_node` 为 `source` / 不存在于树中，或测量线段 id 重复 / 不存在于树中 |
| `400` | `baseline_violation` | `/capacity` 基线校核已存在超额定电流或欠压，`details` 列出越限线段与节点 |

注意：`/unregistered-load` 的 `inconsistent` / `undetermined` 是正常分析结论，以 `200` 返回并体现在响应体的 `status` 字段中，不属于上表错误。

## 本地开发与测试

```bash
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload --port 8000
```

## 项目结构

```
app/
  main.py    # FastAPI 应用与错误处理
  core.py    # 树校验 + 电流累计 + 压降计算 + 增容评估 + 未登记负载定位 + 峰值并发风险
  models.py  # 请求 / 响应模型
tests/
  test_evaluate.py
  test_capacity.py
  test_unregistered_load.py
  test_peak_risk.py
Dockerfile
docker-compose.yml
requirements.txt
```

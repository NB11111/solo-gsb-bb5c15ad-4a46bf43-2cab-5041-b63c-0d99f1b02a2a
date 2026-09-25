"""POST /peak-risk 的端到端测试。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def base_payload(**overrides):
    """与 test_evaluate 相同的小树：

    source --s1(0.1Ω, 20A)--> a --s2(0.2Ω, 10A)--> b
                              |
                              +--s3(0.05Ω, 15A)--> c

    基线：段电流 s1=15, s2=5, s3=8；节点电压 a=46.5, b=45.5, c=46.1
    """
    payload = {
        "source_voltage": 48.0,
        "min_voltage": 40.0,
        "segments": [
            {"id": "s1", "parent": "source", "child": "a", "resistance": 0.1, "rated_current": 20.0},
            {"id": "s2", "parent": "a", "child": "b", "resistance": 0.2, "rated_current": 10.0},
            {"id": "s3", "parent": "a", "child": "c", "resistance": 0.05, "rated_current": 15.0},
        ],
        "loads": [
            {"node": "a", "current": 2.0, "peak_current": 5.0},   # 序号 1，Δ3
            {"node": "b", "current": 5.0, "peak_current": 9.0},   # 序号 2，Δ4
            {"node": "c", "current": 8.0},                         # 序号 3，无峰值增量
        ],
        "max_simultaneous_peaks": 1,
    }
    payload.update(overrides)
    return payload


def segmap(data):
    return {s["segment_id"]: s for s in data["segments"]}


def nodemap(data):
    return {n["node"]: n for n in data["nodes"]}


# ---------- 基本最坏工况（K=1，各约束独立选负载） ----------


def test_worst_currents_k1():
    data = client.post("/peak-risk", json=base_payload()).json()

    s = segmap(data)
    assert s["s1"]["current"] == pytest.approx(15.0)
    assert s["s1"]["worst_current"] == pytest.approx(19.0)  # 取序号 2（Δ4）
    assert s["s1"]["peak_load_indices"] == [2]
    assert s["s1"]["over_rated"] is False
    assert s["s2"]["worst_current"] == pytest.approx(9.0)
    assert s["s2"]["peak_load_indices"] == [2]
    assert s["s3"]["worst_current"] == pytest.approx(8.0)  # 下游负载 3 无增量
    assert s["s3"]["peak_load_indices"] == []


def test_worst_voltages_k1():
    data = client.post("/peak-risk", json=base_payload()).json()

    n = nodemap(data)
    # 序号 2（b, Δ4）：a/c 与 b 共享 s1，附加压降 0.4V；b 自身路径 0.3Ω，1.2V
    assert n["source"]["worst_voltage"] == pytest.approx(48.0)
    assert n["source"]["peak_load_indices"] == []
    assert n["a"]["worst_voltage"] == pytest.approx(46.1)
    assert n["a"]["peak_load_indices"] == [2]
    assert n["b"]["worst_voltage"] == pytest.approx(44.3)
    assert n["b"]["peak_load_indices"] == [2]
    assert n["c"]["worst_voltage"] == pytest.approx(45.7)
    assert n["c"]["peak_load_indices"] == [2]
    assert data["ok"] is True
    assert data["over_current_segments"] == []
    assert data["under_voltage_nodes"] == []


def test_different_constraints_different_combos():
    """线段 s2 的最坏组合与其兄弟节点 c 的最坏组合可以不同。"""
    payload = base_payload(
        loads=[
            {"node": "b", "current": 5.0, "peak_current": 9.0},    # 序号 1，Δ4
            {"node": "c", "current": 8.0, "peak_current": 28.0},   # 序号 2，Δ20
        ]
    )
    data = client.post("/peak-risk", json=payload).json()

    s, n = segmap(data), nodemap(data)
    # s2 下游只有序号 1
    assert s["s2"]["peak_load_indices"] == [1]
    assert s["s2"]["worst_current"] == pytest.approx(9.0)
    # 节点 c：序号 2 自身路径 0.05Ω × 20 = 1.0V > 序号 1 共享 s1 的 0.4V
    assert n["c"]["peak_load_indices"] == [2]
    # 节点 b：序号 2 经公共上游 s1 的附加压降 0.1×20=2.0V > 序号 1 自身 1.2V
    assert n["b"]["peak_load_indices"] == [2]


# ---------- K=2 越限 ----------


def test_over_current_violations_k2():
    data = client.post("/peak-risk", json=base_payload(max_simultaneous_peaks=2)).json()

    s = segmap(data)
    assert s["s1"]["worst_current"] == pytest.approx(22.0)  # 15+4+3
    assert s["s1"]["peak_load_indices"] == [2, 1]           # 贡献降序
    assert s["s1"]["over_rated"] is True
    assert s["s2"]["worst_current"] == pytest.approx(9.0)
    assert s["s2"]["peak_load_indices"] == [2]

    violations = {v["segment_id"]: v for v in data["over_current_segments"]}
    assert set(violations) == {"s1"}
    assert violations["s1"]["current"] == pytest.approx(22.0)
    assert violations["s1"]["excess"] == pytest.approx(2.0)
    assert violations["s1"]["peak_load_indices"] == [2, 1]
    assert data["ok"] is False

    n = nodemap(data)
    assert n["a"]["worst_voltage"] == pytest.approx(45.8)   # 0.4+0.3
    assert n["a"]["peak_load_indices"] == [2, 1]
    assert n["b"]["worst_voltage"] == pytest.approx(44.0)   # 1.2+0.3
    assert n["c"]["worst_voltage"] == pytest.approx(45.4)


# ---------- 贡献相同按输入序号 ----------


def test_equal_contribution_tie_by_input_index():
    payload = base_payload(
        loads=[
            {"node": "b", "current": 1.0, "peak_current": 4.0},  # 序号 1，Δ3
            {"node": "a", "current": 1.0, "peak_current": 1.0},  # 序号 2，Δ0
            {"node": "b", "current": 1.0, "peak_current": 4.0},  # 序号 3，Δ3
        ]
    )
    data = client.post("/peak-risk", json=payload).json()
    s = segmap(data)
    assert s["s2"]["worst_current"] == pytest.approx(5.0)  # 基线 2 + 3
    assert s["s2"]["peak_load_indices"] == [1]             # 同贡献取序号小

    data = client.post(
        "/peak-risk", json=base_payload(
            max_simultaneous_peaks=2,
            loads=[
                {"node": "b", "current": 1.0, "peak_current": 4.0},
                {"node": "a", "current": 1.0, "peak_current": 1.0},
                {"node": "b", "current": 1.0, "peak_current": 4.0},
            ],
        )
    ).json()
    s = segmap(data)
    assert s["s2"]["worst_current"] == pytest.approx(8.0)
    assert s["s2"]["peak_load_indices"] == [1, 3]


def test_same_node_multiple_loads_counted_separately():
    """同一节点两条负载分别计数，K=1 只升其中一条。"""
    payload = base_payload(
        loads=[
            {"node": "b", "current": 2.0, "peak_current": 6.0},  # 序号 1，Δ4
            {"node": "b", "current": 2.0, "peak_current": 5.0},  # 序号 2，Δ3
        ]
    )
    data = client.post("/peak-risk", json=payload).json()
    s = segmap(data)
    assert s["s2"]["worst_current"] == pytest.approx(8.0)  # 基线 4 + 4
    assert s["s2"]["peak_load_indices"] == [1]

    data = client.post("/peak-risk", json={**payload, "max_simultaneous_peaks": 2}).json()
    s = segmap(data)
    assert s["s2"]["worst_current"] == pytest.approx(11.0)
    assert s["s2"]["peak_load_indices"] == [1, 2]
    violations = {v["segment_id"]: v for v in data["over_current_segments"]}
    assert violations["s2"]["excess"] == pytest.approx(1.0)


# ---------- 缺省 / K=0 ----------


def test_default_peak_current_equals_current():
    payload = base_payload(loads=[{"node": "a", "current": 2.0}, {"node": "b", "current": 5.0}])
    data = client.post("/peak-risk", json=payload).json()
    for s in data["segments"]:
        assert s["worst_current"] == pytest.approx(s["current"])
        assert s["peak_load_indices"] == []
    for n in data["nodes"]:
        assert n["worst_voltage"] == pytest.approx(n["voltage"])
        assert n["peak_load_indices"] == []
    assert data["ok"] is True


def test_k_zero_equals_baseline():
    data = client.post(
        "/peak-risk", json=base_payload(max_simultaneous_peaks=0)
    ).json()
    for s in data["segments"]:
        assert s["worst_current"] == pytest.approx(s["current"])
        assert s["peak_load_indices"] == []
    for n in data["nodes"]:
        assert n["worst_voltage"] == pytest.approx(n["voltage"])
        assert n["peak_load_indices"] == []


# ---------- 欠压（含节点级阈值覆盖、兄弟支路经公共上游受影响） ----------


def test_under_voltage_with_sibling_node_override():
    payload = base_payload()
    payload["loads"][2]["min_voltage"] = 46.0  # c 基线 46.1 合格；最坏 45.7 欠压
    data = client.post("/peak-risk", json=payload).json()

    n = nodemap(data)
    assert n["c"]["under_voltage"] is True
    under = {v["node"]: v for v in data["under_voltage_nodes"]}
    assert set(under) == {"c"}
    assert under["c"]["voltage"] == pytest.approx(45.7)
    assert under["c"]["deficit"] == pytest.approx(0.3)
    assert under["c"]["peak_load_indices"] == [2]  # 升峰的是 b 支路负载
    # 线段 s3 本身无升峰负载，但节点 c 仍因公共上游 s1 欠压 → 组合不同
    s = segmap(data)
    assert s["s3"]["peak_load_indices"] == []


def test_exact_threshold_not_violation():
    """最坏值恰好等于额定 / 最低允许电压，按 EPS 容差不算越限。"""
    payload = base_payload()
    payload["segments"][1]["rated_current"] = 9.0  # s2 最坏恰好 9A
    payload["loads"][1]["min_voltage"] = 44.3      # b 最坏恰好 44.3V
    data = client.post("/peak-risk", json=payload).json()

    s, n = segmap(data), nodemap(data)
    assert s["s2"]["over_rated"] is False
    assert n["b"]["under_voltage"] is False
    assert data["ok"] is True


def test_source_load_does_not_affect_tree():
    """source 上的负载升峰不经过任何线段，也不改变任何节点电压。"""
    payload = base_payload(loads=[{"node": "source", "current": 3.0, "peak_current": 9.0}])
    data = client.post("/peak-risk", json=payload).json()
    for s in data["segments"]:
        assert s["worst_current"] == pytest.approx(s["current"])
        assert s["peak_load_indices"] == []
    assert nodemap(data)["source"]["worst_voltage"] == pytest.approx(48.0)


# ---------- 输入校验 ----------


def test_peak_current_smaller_than_current_rejected():
    payload = base_payload()
    payload["loads"][0]["peak_current"] = 1.0
    resp = client.post("/peak-risk", json=payload)
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert any("峰值电流不能小于恒定负载电流" in d["message"] for d in details)


def test_negative_peak_current_rejected():
    payload = base_payload()
    payload["loads"][0]["peak_current"] = -0.1
    resp = client.post("/peak-risk", json=payload)
    assert resp.status_code == 422
    assert any("峰值电流不能为负" in d["message"] for d in resp.json()["error"]["details"])


def test_non_finite_peak_current_rejected():
    body = (
        '{"source_voltage":48.0,"min_voltage":40.0,'
        '"segments":[{"id":"s1","parent":"source","child":"a",'
        '"resistance":0.1,"rated_current":20.0}],'
        '"loads":[{"node":"a","current":1.0,"peak_current":NaN}],'
        '"max_simultaneous_peaks":1}'
    )
    resp = client.post("/peak-risk", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert any("有限数值" in d["message"] for d in resp.json()["error"]["details"])


@pytest.mark.parametrize("value", [-1, 1.5])
def test_invalid_max_simultaneous_peaks_rejected(value):
    resp = client.post("/peak-risk", json=base_payload(max_simultaneous_peaks=value))
    assert resp.status_code == 422
    details = resp.json()["error"]["details"]
    assert any("非负整数" in d["message"] for d in details)


def test_missing_max_simultaneous_peaks_rejected():
    payload = base_payload()
    del payload["max_simultaneous_peaks"]
    resp = client.post("/peak-risk", json=payload)
    assert resp.status_code == 422
    assert any("同时升峰负载上限" in d["message"] for d in resp.json()["error"]["details"])


def test_invalid_tree_rejected():
    payload = base_payload()
    payload["segments"].append(
        {"parent": "x", "child": "y", "resistance": 0.1, "rated_current": 10.0}
    )
    resp = client.post("/peak-risk", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_input"


def test_unknown_load_node_rejected():
    payload = base_payload()
    payload["loads"].append({"node": "ghost", "current": 1.0, "peak_current": 2.0})
    resp = client.post("/peak-risk", json=payload)
    assert resp.status_code == 400
    assert any("不存在" in d["message"] for d in resp.json()["error"]["details"])


def test_index_lists_peak_loads_only_when_relevant():
    """K 大于有贡献的负载数时，返回全部有贡献负载即可。"""
    data = client.post(
        "/peak-risk", json=base_payload(max_simultaneous_peaks=99)
    ).json()
    s = segmap(data)
    # s2 下游仅有序号 2 有增量
    assert s["s2"]["peak_load_indices"] == [2]
    assert s["s2"]["worst_current"] == pytest.approx(9.0)
    # s3 下游序号 3 无增量
    assert s["s3"]["peak_load_indices"] == []

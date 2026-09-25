"""请求 / 响应数据模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError


class SegmentInput(BaseModel):
    """一条线段：电流自父节点流向子节点。"""

    id: Optional[str] = Field(default=None, description="线段标识，缺省自动生成 seg-<序号>")
    parent: str = Field(min_length=1, description="父节点名称，根节点固定为 source")
    child: str = Field(min_length=1, description="子节点名称")
    resistance: float = Field(ge=0, description="线段电阻 (Ω)，不允许为负")
    rated_current: float = Field(gt=0, description="线段额定电流 (A)，必须为正")


class LoadInput(BaseModel):
    """节点恒定负载。"""

    node: str = Field(min_length=1, description="节点名称")
    current: float = Field(ge=0, description="恒定负载电流 (A)，不允许为负")
    min_voltage: Optional[float] = Field(
        default=None, ge=0, description="该节点最低允许电压 (V)，缺省使用全局 min_voltage"
    )


class EvaluateRequest(BaseModel):
    source_voltage: float = Field(gt=0, description="电源电压 (V)，必须为正")
    min_voltage: float = Field(ge=0, description="全局最低允许电压 (V)")
    segments: list[SegmentInput] = Field(
        min_length=1, description="线段列表，须构成以 source 为根的连通树"
    )
    loads: list[LoadInput] = Field(default_factory=list, description="各节点恒定负载电流")


class SegmentResult(BaseModel):
    segment_id: str
    parent: str
    child: str
    current: float = Field(description="线段承载的全部下游电流 (A)")
    rated_current: float = Field(description="线段额定电流 (A)")
    resistance: float = Field(description="线段电阻 (Ω)")
    voltage_drop: float = Field(description="线段压降 (V)")
    over_rated: bool = Field(description="是否超过额定电流")


class NodeResult(BaseModel):
    node: str
    voltage: float = Field(description="节点电压 (V)")
    min_voltage: float = Field(description="该节点生效的最低允许电压 (V)")
    load_current: float = Field(description="节点恒定负载电流 (A)")
    under_voltage: bool = Field(description="是否欠压")


class OverCurrentViolation(BaseModel):
    segment_id: str
    parent: str
    child: str
    current: float
    rated_current: float
    excess: float = Field(description="超出额定值的电流 (A)")


class UnderVoltageViolation(BaseModel):
    node: str
    voltage: float
    min_voltage: float
    deficit: float = Field(description="低于最低允许电压的差值 (V)")


class EvaluateResponse(BaseModel):
    ok: bool = Field(description="无超额定且无欠压时为 true")
    source_voltage: float
    min_voltage: float
    total_source_current: float = Field(description="电源输出的总电流 (A)")
    segments: list[SegmentResult]
    nodes: list[NodeResult]
    over_current_segments: list[OverCurrentViolation] = Field(description="超额定电流的线段")
    under_voltage_nodes: list[UnderVoltageViolation] = Field(description="欠压节点")


class CapacityRequest(EvaluateRequest):
    """增容评估请求：在 /evaluate 参数基础上指定目标节点。"""

    target_node: str = Field(min_length=1, description="目标节点名称（须为已有的非 source 节点）")


class LimitingFactor(BaseModel):
    type: str = Field(description="限制类型：segment=线段额定电流，node=节点最低允许电压")
    id: str = Field(description="达到上限的线段 id 或节点名称")


class CapacityResponse(BaseModel):
    target_node: str = Field(description="目标节点名称")
    max_additional_current: float = Field(
        description="目标节点可增加的最大恒定负载电流 (A)，向下截断至 6 位小数"
    )
    limiting_factors: list[LimitingFactor] = Field(description="达到上限的线段 / 节点")


class MeasurementInput(BaseModel):
    """一条线段的实测电流。"""

    segment_id: str = Field(min_length=1, description="被测线段标识，须唯一且存在于树中")
    current: float = Field(ge=0, allow_inf_nan=False, description="实测电流 (A)，须为非负有限数")


class UnregisteredLoadRequest(EvaluateRequest):
    """未登记负载定位请求：在 /evaluate 参数基础上附加线段实测电流。"""

    measurements: list[MeasurementInput] = Field(
        default_factory=list, description="线段实测电流列表，测点可不覆盖全树"
    )


class MeasurementResult(BaseModel):
    segment_id: str
    baseline_current: float = Field(description="基线线段电流 (A)，展示值按 6 位小数舍入")
    measured_current: float = Field(description="实测电流 (A)")
    difference: float = Field(description="实测电流 - 基线电流 (A)，展示值按 6 位小数舍入")


class UnregisteredLoadResponse(BaseModel):
    status: str = Field(
        description="定位结果：located=已定位候选节点，undetermined=无显著正差无法确定，"
        "inconsistent=实测与基线矛盾"
    )
    load_current: Optional[float] = Field(
        default=None, description="未登记负载电流估计值 (A)，六位小数；status=located 时给出"
    )
    candidate_nodes: list[str] = Field(description="与全部测点相容的节点（BFS 顺序）")
    measurements: list[MeasurementResult] = Field(description="各测点与未舍入基线的对比")
    reasons: list[str] = Field(description="判定为 inconsistent 的原因")


class PeakLoadInput(LoadInput):
    """/peak-risk 的节点负载：在恒定负载基础上可给出峰值电流。"""

    peak_current: Optional[float] = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="峰值负载电流 (A)，须不小于 current；缺省等于 current",
    )

    @model_validator(mode="after")
    def _default_and_check_peak_current(self) -> "PeakLoadInput":
        if self.peak_current is None:
            self.peak_current = self.current
        elif self.peak_current + 1e-9 < self.current:
            raise PydanticCustomError(
                "peak_current_lt_current",
                "峰值电流不能小于恒定负载电流（peak_current 须 ≥ current）",
            )
        return self

class PeakRiskRequest(BaseModel):
    """峰值并发风险校核请求：树与负载沿用 /evaluate，附加峰值参数。"""

    source_voltage: float = Field(gt=0, description="电源电压 (V)，必须为正")
    min_voltage: float = Field(ge=0, description="全局最低允许电压 (V)")
    segments: list[SegmentInput] = Field(
        min_length=1, description="线段列表，须构成以 source 为根的连通树"
    )
    loads: list[PeakLoadInput] = Field(
        default_factory=list,
        description="各节点负载电流（可含 peak_current）；同一节点多条负载分别计数",
    )
    max_simultaneous_peaks: int = Field(
        ge=0, description="最多同时升至峰值的负载条数（非负整数）"
    )


class PeakSegmentResult(BaseModel):
    segment_id: str
    parent: str
    child: str
    current: float = Field(description="基线线段电流 (A)")
    worst_current: float = Field(description="至多 K 条负载升峰时该段的最大承载电流 (A)")
    rated_current: float = Field(description="线段额定电流 (A)")
    peak_load_indices: list[int] = Field(
        description="达到最大承载电流的升峰负载序号（1 起，按贡献降序）"
    )
    over_rated: bool = Field(description="最大承载电流是否超过额定电流")


class PeakNodeResult(BaseModel):
    node: str
    voltage: float = Field(description="基线节点电压 (V)")
    worst_voltage: float = Field(description="至多 K 条负载升峰时该节点的最低电压 (V)")
    min_voltage: float = Field(description="该节点生效的最低允许电压 (V)")
    load_current: float = Field(description="节点恒定负载电流 (A)")
    peak_load_indices: list[int] = Field(
        description="导致最低电压的升峰负载序号（1 起，按贡献降序）"
    )
    under_voltage: bool = Field(description="最低电压是否低于最低允许电压")


class PeakOverCurrentViolation(BaseModel):
    segment_id: str
    parent: str
    child: str
    current: float = Field(description="越限工况下的线段电流 (A)")
    rated_current: float
    excess: float = Field(description="超出额定值的电流 (A)")
    peak_load_indices: list[int] = Field(description="导致越限的升峰负载序号（1 起）")


class PeakUnderVoltageViolation(BaseModel):
    node: str
    voltage: float = Field(description="越限工况下的节点电压 (V)")
    min_voltage: float
    deficit: float = Field(description="低于最低允许电压的差值 (V)")
    peak_load_indices: list[int] = Field(description="导致越限的升峰负载序号（1 起）")


class PeakRiskResponse(BaseModel):
    ok: bool = Field(description="最坏工况下无超额定且无欠压时为 true")
    source_voltage: float
    min_voltage: float
    max_simultaneous_peaks: int
    segments: list[PeakSegmentResult]
    nodes: list[PeakNodeResult]
    over_current_segments: list[PeakOverCurrentViolation] = Field(
        description="最大承载电流超额定的线段"
    )
    under_voltage_nodes: list[PeakUnderVoltageViolation] = Field(
        description="最低电压低于阈值的节点"
    )

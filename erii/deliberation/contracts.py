"""
Provider-neutral 协议定义

本模块定义 Character Deliberation 的 Provider-neutral 协议。
所有 Provider（Claude、DeepSeek、本地模型等）必须实现统一的接口。

设计原则：
- 协议不包含任何供应商专属字段
- 协议不暴露 raw thinking、Prompt、凭据或错误正文
- Actor 只能提出候选，不能声明权威
"""

from __future__ import annotations

from typing import Protocol, TypeVar, Generic
from dataclasses import dataclass
from enum import Enum

from .identifiers import validate_identifier


T = TypeVar("T")


class ProviderErrorCode(str, Enum):
    """Provider 错误归一化代码"""

    # 请求问题
    REQUEST_INVALID = "provider_request_invalid"
    AUTHENTICATION_FAILED = "provider_authentication_failed"
    BILLING_FAILED = "provider_billing_failed"
    PERMISSION_DENIED = "provider_permission_denied"
    NOT_FOUND = "provider_not_found"
    CONFLICT = "provider_conflict"
    REQUEST_TOO_LARGE = "provider_request_too_large"

    # 限流与超时
    RATE_LIMITED = "provider_rate_limited"
    TIMEOUT = "provider_timeout"
    UNAVAILABLE = "provider_unavailable"

    # 输出问题
    REFUSAL = "provider_refusal"
    OUTPUT_TRUNCATED = "provider_output_truncated"
    OUTPUT_SCHEMA_INVALID = "provider_output_schema_invalid"
    OUTPUT_EVIDENCE_INVALID = "provider_output_evidence_invalid"
    OUTPUT_CANARY_LEAK = "provider_output_canary_leak"

    # 能力问题
    CAPABILITY_UNAVAILABLE = "provider_capability_unavailable"

    # 控制
    CANCELLED = "provider_cancelled"
    LATE_RESULT = "provider_late_result"


@dataclass(frozen=True)
class ActorDescriptor:
    """
    Actor 描述符（脱敏、版本化）

    用于实验复现和比较，不包含凭据或账户信息。
    """

    # Provider 识别
    provider_kind: str  # 例如 "anthropic_messages", "deepseek_chat"
    adapter_contract: str  # 例如 "erii-character-deliberation-claude/v1"
    adapter_version: str  # Adapter 的 semver

    # 模型配置（宿主显式配置）
    model_id: str  # 完整版本化模型 ID，不是 "latest"

    # 能力声明
    supports_compact: bool
    supports_staged: bool
    supports_cancellation: bool

    # 实现策略（不暴露具体实现）
    structured_output_strategy: str  # 例如 "json_schema", "strict_tool"

    def __post_init__(self):
        """验证描述符完整性"""
        validate_identifier(self.provider_kind, "provider_kind")
        validate_identifier(self.adapter_contract, "adapter_contract")
        validate_identifier(self.adapter_version, "adapter_version")
        validate_identifier(self.model_id, "model_id")
        validate_identifier(self.structured_output_strategy, "structured_output_strategy")
        if "/" not in self.adapter_contract:
            raise ValueError("adapter_contract 必须包含版本")


@dataclass(frozen=True)
class ProviderUsage:
    """Provider 使用统计（脱敏）"""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    # 延迟指标
    latency_ms: int | None = None
    first_event_ms: int | None = None

    def __post_init__(self):
        if self.input_tokens < 0:
            raise ValueError("input_tokens 必须非负")
        if self.output_tokens < 0:
            raise ValueError("output_tokens 必须非负")


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    """
    Provider 调用结果（统一封装）

    成功时包含已解析的领域对象；失败时包含归一化错误码。
    不包含 raw thinking、Prompt、凭据或原始错误正文。
    """

    # 成功路径
    success: bool
    data: T | None = None

    # 失败路径
    error_code: ProviderErrorCode | None = None
    error_message: str | None = None  # 脱敏后的简短描述

    # 运维元数据（脱敏）
    usage: ProviderUsage | None = None

    # 调试信息（不包含正文）
    discarded_reasoning_blocks: int = 0
    canary_hit: bool = False

    def __post_init__(self):
        """验证结果一致性"""
        if self.success:
            if self.data is None:
                raise ValueError("成功结果必须有 data")
            if self.error_code is not None:
                raise ValueError("成功结果不应有 error_code")
            if self.error_message is not None:
                raise ValueError("successful result must not have error_message")
        else:
            if self.data is not None:
                raise ValueError("失败结果不应有 data")
            if self.error_code is None:
                raise ValueError("失败结果必须有 error_code")
            if self.error_message not in {None, self.error_code.value}:
                raise ValueError("error_message must be the stable normalized error code")
        if type(self.discarded_reasoning_blocks) is not int:
            raise ValueError("discarded_reasoning_blocks must be an int")
        if self.discarded_reasoning_blocks < 0:
            raise ValueError("discarded_reasoning_blocks must be non-negative")
        if type(self.canary_hit) is not bool:
            raise ValueError("canary_hit must be bool")


class CharacterActor(Protocol):
    """
    Character Actor 协议（Provider-neutral）

    所有 Model Provider 必须实现此协议。Actor 只能提出心理候选，
    不能声明 Persona、Relationship、Memory 权威。

    重要约束：
    - Actor 不能绕过 Core 的 evidence、scope、binding 校验
    - Actor 不能自行调用 Recall 扩大证据范围
    - Actor 输出的 thinking 在 Adapter 内立即丢弃
    - Actor 不能修改持久状态或声明 Trusted Envelope 字段
    """

    @property
    def descriptor(self) -> ActorDescriptor:
        """返回 Actor 描述符"""
        ...

    def compact(
        self,
        request: "CompactDeliberationRequestV1",
        *,
        timeout: float,
    ) -> ProviderResult["CompactDecisionV1"]:
        """
        Compact 主路径：一次调用返回完整结果

        Args:
            request: 冻结的、关系隔离的 Deliberation 请求
            timeout: 绝对 deadline（秒），不是每层独立超时

        Returns:
            包含 Frame + Interior Scene + Reply 的候选，或归一化错误

        注意：
            - 成功返回不等于通过 Continuity Review
            - raw thinking 必须在 Adapter 内丢弃
            - 超时后的迟到结果会被 run fencing 丢弃
        """
        ...

    def plan(
        self,
        request: "StagedPlanRequestV1",
        *,
        timeout: float,
    ) -> ProviderResult["DeliberationPlanV1"]:
        """
        Staged 第一阶段：返回 Frame + Interior Scene（不含最终回复）

        Returns:
            完整、可独立校验的心理候选，经 Core 校验后形成 plan_fingerprint
        """
        ...

    def realize(
        self,
        request: "ReplyRealizationRequestV1",
        *,
        timeout: float,
    ) -> ProviderResult["ReplyRealizationV1"]:
        """
        Staged 第二阶段：基于已验证 Plan 产生最终回复

        Args:
            request: 包含同一 Trusted Envelope、Evidence View 和已验证 plan_fingerprint

        Returns:
            绑定 plan 的 Reply Envelope 候选

        注意：
            - 不能重写 Frame 或 Interior Scene
            - 发现 Plan 不足时返回有界错误，不能偷偷加新心理
        """
        ...


# 为了避免循环导入，这里只声明类型，实际定义在 schemas.py
# 运行时会从 schemas 模块导入真实类
if False:  # TYPE_CHECKING 的简化版本
    from erii.deliberation.schemas import (
        CompactDeliberationRequestV1,
        CompactDecisionV1,
        StagedPlanRequestV1,
        DeliberationPlanV1,
        ReplyRealizationRequestV1,
        ReplyRealizationV1,
    )

"""
Core Trusted Validator - 宿主权威状态验证器

本模块实现真正的 Trusted System：
- 宿主持有秘密（HMAC key）
- Envelope 使用 HMAC 签名而非自签名 SHA-256
- 只有持有秘密的可信代码才能创建和验证
- Actor 无法伪造

设计原则：
1. 秘密只在宿主 Core 中持有
2. Envelope 使用 HMAC 签名
3. 验证时比较宿主的权威状态
4. 包含完整的 Reply/Result/Revision binding
"""

from __future__ import annotations

import hmac
import hashlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from erii.models.turn import TurnStatus

from .identifiers import validate_identifier

if TYPE_CHECKING:
    from erii.deliberation.schemas import (
        VisibleReplyEnvelopeV1,
        CompactDecisionV1,
    )


class TrustedAuthoritySecret:
    """
    Trusted Authority Secret（宿主持有的秘密）

    只有宿主 Core 持有此秘密，Actor 无法访问。
    用于签名和验证 Trusted Envelope。
    """

    def __init__(self, secret_key: bytes | None = None):
        """
        初始化秘密

        Args:
            secret_key: 32 字节秘密。如果为 None，生成新秘密。
                       生产环境应从安全存储加载。
        """
        if secret_key is None:
            # 生成新秘密（生产环境应从配置加载）
            # 使用 secrets 模块生成加密安全的随机字节
            self._secret = os.urandom(32)
        else:
            if type(secret_key) is not bytes:
                raise TypeError("secret_key must be bytes")
            if len(secret_key) != 32:
                raise ValueError("secret_key 必须是 32 字节")
            self._secret = bytes(secret_key)

    def sign(self, message: str) -> str:
        """
        使用 HMAC-SHA256 签名消息

        Args:
            message: 要签名的消息（通常是规范化的 JSON）

        Returns:
            十六进制签名
        """
        if type(message) is not str:
            raise TypeError("message must be a string")
        h = hmac.new(self._secret, message.encode('utf-8'), hashlib.sha256)
        return h.hexdigest()

    def verify(self, message: str, signature: str) -> bool:
        """
        验证 HMAC 签名

        Args:
            message: 原始消息
            signature: 声称的签名

        Returns:
            签名是否有效
        """
        if type(signature) is not str or len(signature) != 64:
            return False
        expected = self.sign(message)
        return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class AuthorityState:
    """
    Authority State（宿主权威状态）

    宿主维护的当前状态，用于验证 Envelope 和 Result。
    """

    # 当前 epoch（单调递增）
    current_epoch: int

    # 当前 Turn 状态（真实的 erii.models.turn.TurnStatus）
    turn_status: TurnStatus

    # 当前 Relationship
    active_relationship_id: str

    # 当前 Turn
    active_turn_id: str

    # 当前 Persona
    active_persona_id: str

    def __post_init__(self):
        """验证所有字段"""
        # 验证 epoch
        if not isinstance(self.current_epoch, int) or isinstance(self.current_epoch, bool):
            raise ValueError(
                f"current_epoch 必须是 int 类型，不能是 {type(self.current_epoch).__name__}"
            )
        if self.current_epoch < 0:
            raise ValueError(f"current_epoch 必须非负，实际 {self.current_epoch}")

        # 验证 turn_status 是真实的 TurnStatus 枚举
        if not isinstance(self.turn_status, TurnStatus):
            raise ValueError(
                f"turn_status 必须是 erii.models.turn.TurnStatus 枚举，不能是 {type(self.turn_status).__name__}"
            )

        # 验证 ID 字符串
        _validate_id_string(self.active_relationship_id, "active_relationship_id")
        _validate_id_string(self.active_turn_id, "active_turn_id")
        _validate_id_string(self.active_persona_id, "active_persona_id")


@dataclass(frozen=True)
class TrustedEnvelopeV2:
    """
    Trusted Envelope V2（使用 HMAC 签名）

    由持有秘密的可信代码构造和验证。
    Actor 无法伪造，因为没有秘密。
    """

    # 核心身份
    relationship_id: str
    turn_id: str
    persona_id: str

    # Binding fingerprints
    evidence_view_fingerprint: str
    user_message_fingerprint: str

    # 运行态控制
    run_epoch: int
    expected_turn_state: str

    # HMAC 签名（使用宿主秘密）
    hmac_signature: str

    def __post_init__(self) -> None:
        _validate_id_string(self.relationship_id, "relationship_id")
        _validate_id_string(self.turn_id, "turn_id")
        _validate_id_string(self.persona_id, "persona_id")
        _validate_fingerprint(self.evidence_view_fingerprint, "evidence_view_fingerprint")
        _validate_fingerprint(self.user_message_fingerprint, "user_message_fingerprint")
        if type(self.run_epoch) is not int or self.run_epoch < 0:
            raise ValueError("run_epoch must be a non-negative int")
        if self.expected_turn_state != TurnStatus.OPEN.value:
            raise ValueError("trusted deliberation envelope requires an open turn")
        _validate_fingerprint(self.hmac_signature, "hmac_signature")

    @staticmethod
    def compute_message(
        relationship_id: str,
        turn_id: str,
        persona_id: str,
        evidence_view_fingerprint: str,
        user_message_fingerprint: str,
        run_epoch: int,
        expected_turn_state: str,
    ) -> str:
        """计算要签名的规范化消息"""
        import json

        data = {
            "binding_kind": "erii-deliberation-trusted-envelope/v2",
            "relationship_id": relationship_id,
            "turn_id": turn_id,
            "persona_id": persona_id,
            "evidence_view_fingerprint": evidence_view_fingerprint,
            "user_message_fingerprint": user_message_fingerprint,
            "run_epoch": run_epoch,
            "expected_turn_state": expected_turn_state,
        }
        return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

    def verify_with_secret(self, secret: TrustedAuthoritySecret) -> bool:
        """使用宿主秘密验证签名"""
        message = self.compute_message(
            self.relationship_id,
            self.turn_id,
            self.persona_id,
            self.evidence_view_fingerprint,
            self.user_message_fingerprint,
            self.run_epoch,
            self.expected_turn_state,
        )
        return secret.verify(message, self.hmac_signature)

    def verify_against_authority(self, authority: AuthorityState) -> tuple[bool, list[str]]:
        """
        验证 Envelope 与宿主权威状态一致

        这是关键：即使签名有效，也必须与宿主当前状态匹配。
        """
        errors = []

        # 验证 epoch（必须匹配当前 epoch）
        if self.run_epoch != authority.current_epoch:
            errors.append(
                f"Epoch 不匹配: envelope={self.run_epoch} vs authority={authority.current_epoch}"
            )

        # 验证 relationship
        if self.relationship_id != authority.active_relationship_id:
            errors.append(
                f"Relationship 不匹配: envelope={self.relationship_id} "
                f"vs authority={authority.active_relationship_id}"
            )

        # 验证 turn
        if self.turn_id != authority.active_turn_id:
            errors.append(
                f"Turn 不匹配: envelope={self.turn_id} vs authority={authority.active_turn_id}"
            )

        # 验证 persona
        if self.persona_id != authority.active_persona_id:
            errors.append(
                f"Persona 不匹配: envelope={self.persona_id} "
                f"vs authority={authority.active_persona_id}"
            )

        # 验证状态（Envelope 保存字符串值，需与 AuthorityState.turn_status.value 匹配）
        if self.expected_turn_state != authority.turn_status.value:
            errors.append(
                f"Turn state 不匹配: envelope={self.expected_turn_state} "
                f"vs authority={authority.turn_status.value}"
            )

        return len(errors) == 0, errors


@dataclass(frozen=True)
class ResultBinding:
    """
    Result Binding（结果绑定）

    将 Actor 返回的结果与 Trusted Envelope 和最终 Reply 绑定。
    """

    # 源 Envelope 的 fingerprint
    envelope_fingerprint: str

    # Decision 的 fingerprint
    decision_fingerprint: str

    # Reply 的 fingerprint
    reply_fingerprint: str

    # Result 的 fingerprint（用于 Turn 记录）
    result_fingerprint: str

    # HMAC 签名（使用宿主秘密）
    hmac_signature: str

    def __post_init__(self) -> None:
        _validate_fingerprint(self.envelope_fingerprint, "envelope_fingerprint")
        _validate_fingerprint(self.decision_fingerprint, "decision_fingerprint")
        _validate_fingerprint(self.reply_fingerprint, "reply_fingerprint")
        _validate_fingerprint(self.result_fingerprint, "result_fingerprint")
        _validate_fingerprint(self.hmac_signature, "hmac_signature")

    @staticmethod
    def compute_message(
        envelope_fingerprint: str,
        decision_fingerprint: str,
        reply_fingerprint: str,
        result_fingerprint: str,
    ) -> str:
        """计算要签名的规范化消息"""
        import json

        data = {
            "binding_kind": "erii-deliberation-result-binding/v1",
            "envelope_fingerprint": envelope_fingerprint,
            "decision_fingerprint": decision_fingerprint,
            "reply_fingerprint": reply_fingerprint,
            "result_fingerprint": result_fingerprint,
        }
        return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

    def verify_with_secret(self, secret: TrustedAuthoritySecret) -> bool:
        """使用宿主秘密验证签名"""
        message = self.compute_message(
            self.envelope_fingerprint,
            self.decision_fingerprint,
            self.reply_fingerprint,
            self.result_fingerprint,
        )
        return secret.verify(message, self.hmac_signature)


class CoreTrustedValidator:
    """
    Core Trusted Validator（核心可信验证器）

    由宿主 Core 持有，负责：
    1. 创建 Trusted Envelope（使用秘密签名）
    2. 验证 Envelope（使用秘密和权威状态）
    3. 创建 Result Binding（绑定最终结果）
    4. 验证 Result Binding
    """

    def __init__(self, secret: TrustedAuthoritySecret):
        """
        初始化验证器

        Args:
            secret: 宿主持有的秘密（Actor 无法访问）
        """
        self._secret = secret

    def create_envelope(
        self,
        relationship_id: str,
        turn_id: str,
        persona_id: str,
        evidence_view_fingerprint: str,
        user_message_fingerprint: str,
        run_epoch: int,
        expected_turn_state: TurnStatus,
    ) -> TrustedEnvelopeV2:
        """
        创建 Trusted Envelope（仅限可信代码调用）

        使用宿主秘密签名，Actor 无法伪造。

        Args:
            relationship_id: 关系 ID（不能为空）
            turn_id: Turn ID（不能为空）
            persona_id: Persona ID（不能为空）
            evidence_view_fingerprint: Evidence 指纹（64 位小写十六进制）
            user_message_fingerprint: User Message 指纹（64 位小写十六进制）
            run_epoch: 运行 epoch（严格 int，非负）
            expected_turn_state: 预期状态（TurnStatus 枚举）

        Raises:
            ValueError: 如果参数无效
        """
        # 验证 epoch 类型和范围
        if not isinstance(run_epoch, int) or isinstance(run_epoch, bool):
            raise ValueError(f"run_epoch 必须是 int 类型，不能是 {type(run_epoch).__name__}")
        if run_epoch < 0:
            raise ValueError(f"run_epoch 必须非负，实际 {run_epoch}")

        # 验证状态枚举
        if not isinstance(expected_turn_state, TurnStatus):
            raise ValueError(
                f"expected_turn_state 必须是 TurnStatus 枚举，不能是 {type(expected_turn_state).__name__}"
            )
        if expected_turn_state is not TurnStatus.OPEN:
            raise ValueError("character deliberation requires an open source turn")

        # 验证 ID 字符串
        _validate_id_string(relationship_id, "relationship_id")
        _validate_id_string(turn_id, "turn_id")
        _validate_id_string(persona_id, "persona_id")

        # 验证 fingerprint 格式
        _validate_fingerprint(evidence_view_fingerprint, "evidence_view_fingerprint")
        _validate_fingerprint(user_message_fingerprint, "user_message_fingerprint")

        message = TrustedEnvelopeV2.compute_message(
            relationship_id,
            turn_id,
            persona_id,
            evidence_view_fingerprint,
            user_message_fingerprint,
            run_epoch,
            expected_turn_state.value,  # 使用枚举值
        )

        signature = self._secret.sign(message)

        return TrustedEnvelopeV2(
            relationship_id=relationship_id,
            turn_id=turn_id,
            persona_id=persona_id,
            evidence_view_fingerprint=evidence_view_fingerprint,
            user_message_fingerprint=user_message_fingerprint,
            run_epoch=run_epoch,
            expected_turn_state=expected_turn_state.value,
            hmac_signature=signature,
        )

    def verify_envelope(
        self,
        envelope: TrustedEnvelopeV2,
        authority: AuthorityState,
    ) -> tuple[bool, list[str]]:
        """
        验证 Envelope

        必须同时满足：
        1. HMAC 签名有效（使用秘密）
        2. 与宿主权威状态一致
        """
        errors = []

        # 验证签名
        if not envelope.verify_with_secret(self._secret):
            errors.append("HMAC 签名无效")
            return False, errors

        # 验证与权威状态一致
        authority_valid, authority_errors = envelope.verify_against_authority(authority)
        if not authority_valid:
            errors.extend(authority_errors)

        return len(errors) == 0, errors

    def create_result_binding(
        self,
        envelope: TrustedEnvelopeV2,
        decision: "CompactDecisionV1",
        reply: "VisibleReplyEnvelopeV1",
        authority: AuthorityState,
    ) -> ResultBinding:
        """
        创建 Result Binding（绑定最终结果）

        将 Envelope、Decision、Reply 绑定到一起，用于 Turn 记录。

        关键验证：
        1. Envelope 的 HMAC 必须有效
        2. Envelope 必须与当前 Authority 一致
        3. Reply 必须与 decision.reply_candidate 完全相等

        Args:
            envelope: 源 Envelope（必须已验证）
            decision: Actor 返回的 Decision
            reply: 最终 Reply
            authority: 当前权威状态

        Raises:
            ValueError: 如果验证失败
        """
        if authority.turn_status is not TurnStatus.OPEN:
            raise ValueError("character deliberation requires an open source turn")

        # 1. 验证 Envelope HMAC
        if not envelope.verify_with_secret(self._secret):
            raise ValueError("Envelope HMAC 签名无效")

        # 2. 验证 Envelope 与 Authority 一致
        authority_valid, authority_errors = envelope.verify_against_authority(authority)
        if not authority_valid:
            raise ValueError(f"Envelope 与 Authority 不一致: {'; '.join(authority_errors)}")

        # 3. 验证 Reply 完全相等
        if reply != decision.reply_candidate:
            raise ValueError(
                "Reply 必须与 decision.reply_candidate 完全相等"
            )

        from erii.deliberation.strict_codec import StrictCanonicalCodec

        # 计算各部分的 fingerprint（使用 Strict Codec）
        envelope_data = {
            "relationship_id": envelope.relationship_id,
            "turn_id": envelope.turn_id,
            "persona_id": envelope.persona_id,
            "evidence_view_fingerprint": envelope.evidence_view_fingerprint,
            "user_message_fingerprint": envelope.user_message_fingerprint,
            "run_epoch": envelope.run_epoch,
            "expected_turn_state": envelope.expected_turn_state,
        }
        envelope_fp = StrictCanonicalCodec.fingerprint(envelope_data)

        decision_data = decision.model_dump(mode='json')
        decision_fp = StrictCanonicalCodec.fingerprint(decision_data)

        reply_data = reply.model_dump(mode='json')
        reply_fp = StrictCanonicalCodec.fingerprint(reply_data)

        # 计算完整 result fingerprint
        result_data = {
            "envelope": envelope_fp,
            "decision": decision_fp,
            "reply": reply_fp,
        }
        result_fp = StrictCanonicalCodec.fingerprint(result_data)

        # 签名
        message = ResultBinding.compute_message(
            envelope_fp,
            decision_fp,
            reply_fp,
            result_fp,
        )
        signature = self._secret.sign(message)

        return ResultBinding(
            envelope_fingerprint=envelope_fp,
            decision_fingerprint=decision_fp,
            reply_fingerprint=reply_fp,
            result_fingerprint=result_fp,
            hmac_signature=signature,
        )

    def verify_result_binding(
        self,
        binding: ResultBinding,
        envelope: TrustedEnvelopeV2,
        decision: "CompactDecisionV1",
        reply: "VisibleReplyEnvelopeV1",
        authority: AuthorityState,
    ) -> tuple[bool, list[str]]:
        """
        验证 Result Binding

        重新计算所有 fingerprint 并验证 HMAC。

        Args:
            binding: 要验证的 Binding
            envelope: 实际的 Envelope
            decision: 实际的 Decision
            reply: 实际的 Reply
            authority: 当前权威状态

        Returns:
            (是否有效, 错误列表)
        """
        errors = []

        if authority.turn_status is not TurnStatus.OPEN:
            return False, ["source turn is not open"]

        # 1. 验证 Envelope HMAC
        if not envelope.verify_with_secret(self._secret):
            errors.append("Envelope HMAC 签名无效")
            return False, errors

        # 2. 验证 Envelope 与 Authority 一致
        authority_valid, authority_errors = envelope.verify_against_authority(authority)
        if not authority_valid:
            errors.extend(authority_errors)
            return False, errors

        # 3. 验证 Reply 相等
        if reply != decision.reply_candidate:
            errors.append("Reply 与 decision.reply_candidate 不一致")

        # 4. 重新计算所有 fingerprint
        from erii.deliberation.strict_codec import StrictCanonicalCodec

        envelope_data = {
            "relationship_id": envelope.relationship_id,
            "turn_id": envelope.turn_id,
            "persona_id": envelope.persona_id,
            "evidence_view_fingerprint": envelope.evidence_view_fingerprint,
            "user_message_fingerprint": envelope.user_message_fingerprint,
            "run_epoch": envelope.run_epoch,
            "expected_turn_state": envelope.expected_turn_state,
        }
        actual_envelope_fp = StrictCanonicalCodec.fingerprint(envelope_data)

        decision_data = decision.model_dump(mode='json')
        actual_decision_fp = StrictCanonicalCodec.fingerprint(decision_data)

        reply_data = reply.model_dump(mode='json')
        actual_reply_fp = StrictCanonicalCodec.fingerprint(reply_data)

        # 5. 重新计算 result_fingerprint
        result_data = {
            "envelope": actual_envelope_fp,
            "decision": actual_decision_fp,
            "reply": actual_reply_fp,
        }
        actual_result_fp = StrictCanonicalCodec.fingerprint(result_data)

        # 6. 验证 fingerprint 匹配
        if actual_envelope_fp != binding.envelope_fingerprint:
            errors.append("Envelope fingerprint 不匹配")

        if actual_decision_fp != binding.decision_fingerprint:
            errors.append("Decision fingerprint 不匹配")

        if actual_reply_fp != binding.reply_fingerprint:
            errors.append("Reply fingerprint 不匹配")

        # 关键：验证 result_fingerprint
        if actual_result_fp != binding.result_fingerprint:
            errors.append("Result fingerprint 不一致")

        # 7. 验证 Binding HMAC
        if not binding.verify_with_secret(self._secret):
            errors.append("Binding HMAC 签名无效")

        return len(errors) == 0, errors


__all__ = [
    'TrustedAuthoritySecret',
    'AuthorityState',
    'TrustedEnvelopeV2',
    'ResultBinding',
    'CoreTrustedValidator',
]


def _validate_fingerprint(fp: str, field_name: str) -> None:
    """
    验证 fingerprint 格式

    Args:
        fp: fingerprint 字符串
        field_name: 字段名（用于错误消息）

    Raises:
        ValueError: 如果格式无效
    """
    if type(fp) is not str:
        raise ValueError(f"{field_name} 必须是字符串")
    if len(fp) != 64:
        raise ValueError(f"{field_name} 长度必须是 64，实际 {len(fp)}")
    if not all(c in '0123456789abcdef' for c in fp):
        raise ValueError(f"{field_name} 必须是小写十六进制")


def _validate_id_string(id_str: str, field_name: str, max_length: int = 256) -> None:
    """
    验证 ID 字符串（标识符规则，不是 narrative 文本）

    ID 不允许任何控制字符、格式字符、代理字符、行/段分隔符。

    Args:
        id_str: ID 字符串
        field_name: 字段名
        max_length: 最大长度

    Raises:
        ValueError: 如果无效
    """
    validate_identifier(id_str, field_name, max_length=max_length)

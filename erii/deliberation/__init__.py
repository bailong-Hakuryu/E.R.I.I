"""Experimental Character Deliberation Labs surface.

This package is removable, provider-neutral, process-local, and has no
compatibility guarantee while C0 is active. Importing it does not enable any
runtime feature, network call, persistence, or background work.
"""

from .contracts import (
    ActorDescriptor,
    CharacterActor,
    ProviderErrorCode,
    ProviderResult,
    ProviderUsage,
)
from .schemas import (
    CharacterInteriorSceneV1,
    CompactDecisionV1,
    CompactDeliberationRequestV1,
    DeliberationSemanticFrameV1,
    EvidenceItem,
    EvidenceViewV1,
    MessagePart,
    ResultKind,
    UserMessageEnvelope,
    VisibleReplyEnvelopeV1,
)
from .orchestration import (
    CompactDeliberationOrchestrator,
    DeliberationMode,
    EngineDeliberationRuntime,
    PreparationFailureCode,
    PreparedVisibleReplyV1,
    ReplyPreparationOutcomeV1,
    ReplySource,
    build_user_envelope,
)

__all__ = [
    "ActorDescriptor",
    "CharacterActor",
    "ProviderErrorCode",
    "ProviderResult",
    "ProviderUsage",
    "CharacterInteriorSceneV1",
    "CompactDecisionV1",
    "CompactDeliberationRequestV1",
    "DeliberationSemanticFrameV1",
    "EvidenceItem",
    "EvidenceViewV1",
    "MessagePart",
    "ResultKind",
    "UserMessageEnvelope",
    "VisibleReplyEnvelopeV1",
    "CompactDeliberationOrchestrator",
    "DeliberationMode",
    "EngineDeliberationRuntime",
    "PreparationFailureCode",
    "PreparedVisibleReplyV1",
    "ReplyPreparationOutcomeV1",
    "ReplySource",
    "build_user_envelope",
]

__status__ = "experimental"

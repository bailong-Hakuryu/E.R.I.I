"""Deep module for source-grounded Persona Compilation review."""

from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping, Optional, Tuple, Union
import uuid

from erii.adapters.persona_compiler import (
    BasePersonaCompilerAdapter,
    CallablePersonaCompilerAdapter,
    PersonaCompilerAdapterLike,
)
from erii.models.persona import (
    PersonaCompilationConflictError,
    PersonaCompilationDecision,
    PersonaCompilationProposal,
    PersonaCompilationStatus,
    PersonaManifest,
    PersonaManifestCandidate,
)
from erii.models.relationship import CharacterBlueprint, utc_now


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PersonaCompiler:
    """Validates compiler output and manages immutable proposal revisions.

    The module has no persistence side effects. Callers persist returned
    proposal revisions and manifests atomically through their storage adapter.
    """

    @classmethod
    def compile(
        cls,
        blueprint: CharacterBlueprint,
        adapter: PersonaCompilerAdapterLike,
        *,
        proposal_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> PersonaCompilationProposal:
        """Explicitly runs one adapter and returns a validated pending proposal."""
        resolved = adapter
        if callable(adapter) and not isinstance(adapter, BasePersonaCompilerAdapter):
            resolved = CallablePersonaCompilerAdapter(adapter)
        if not isinstance(resolved, BasePersonaCompilerAdapter):
            raise TypeError("adapter must be a BasePersonaCompilerAdapter or callable")
        candidate = resolved.compile(blueprint)
        return cls.propose(
            blueprint,
            candidate,
            proposal_id=proposal_id,
            created_by=created_by,
        )

    @classmethod
    def propose(
        cls,
        blueprint: CharacterBlueprint,
        candidate: Union[PersonaManifestCandidate, Mapping[str, Any]],
        *,
        proposal_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> PersonaCompilationProposal:
        """Validates a complete candidate against exact Blueprint source spans."""
        blueprint_id, revision, source_text, source_sha256 = cls._blueprint_identity(blueprint)
        validated = cls._validate_against_source(candidate, source_text)
        fingerprint = cls.content_fingerprint(
            blueprint_id,
            revision,
            source_sha256,
            validated,
        )
        stable_id = proposal_id or str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"erii:persona-compilation:{blueprint_id}:{revision}:{fingerprint}",
            )
        )
        return PersonaCompilationProposal(
            proposal_id=stable_id,
            revision=1,
            blueprint_id=blueprint_id,
            blueprint_revision=revision,
            source_sha256=source_sha256,
            candidate=validated,
            content_fingerprint=fingerprint,
            created_by=created_by,
        )

    @classmethod
    def revise(
        cls,
        blueprint: CharacterBlueprint,
        current: PersonaCompilationProposal,
        candidate: Union[PersonaManifestCandidate, Mapping[str, Any]],
        *,
        expected_revision: int,
        actor_id: str,
    ) -> PersonaCompilationProposal:
        """Creates a new immutable revision from a current pending revision."""
        if current.revision != expected_revision:
            raise PersonaCompilationConflictError("persona proposal revision changed")
        if current.status != PersonaCompilationStatus.PENDING:
            raise PersonaCompilationConflictError("only a pending proposal may be revised")
        actor_id = cls._require_text(actor_id, "actor_id")
        blueprint_id, revision, source_text, source_sha256 = cls._blueprint_identity(blueprint)
        if (
            blueprint_id != current.blueprint_id
            or revision != current.blueprint_revision
            or source_sha256 != current.source_sha256
        ):
            raise PersonaCompilationConflictError(
                "proposal revision belongs to a different Character Blueprint revision"
            )
        validated = cls._validate_against_source(candidate, source_text)
        fingerprint = cls.content_fingerprint(
            blueprint_id,
            revision,
            source_sha256,
            validated,
        )
        if fingerprint == current.content_fingerprint:
            raise PersonaCompilationConflictError("persona proposal revision has no content change")
        return PersonaCompilationProposal(
            proposal_id=current.proposal_id,
            revision=current.revision + 1,
            parent_revision=current.revision,
            blueprint_id=blueprint_id,
            blueprint_revision=revision,
            source_sha256=source_sha256,
            candidate=validated,
            content_fingerprint=fingerprint,
            created_by=actor_id,
        )

    @classmethod
    def decide(
        cls,
        proposal: PersonaCompilationProposal,
        *,
        revision: int,
        actor_id: str,
        decision: Union[PersonaCompilationDecision, str],
        reason: Optional[str] = None,
        decided_at: Optional[str] = None,
    ) -> PersonaCompilationProposal:
        """Applies an out-of-band decision to an exact proposal revision."""
        if proposal.revision != revision:
            raise PersonaCompilationConflictError("persona proposal revision changed")
        actor_id = cls._require_text(actor_id, "actor_id")
        parsed_decision = PersonaCompilationDecision(decision)
        if parsed_decision == PersonaCompilationDecision.REVOKE:
            if proposal.status != PersonaCompilationStatus.APPROVED:
                raise PersonaCompilationConflictError("only an approved proposal may be revoked")
            status = PersonaCompilationStatus.REVOKED
        else:
            if proposal.status != PersonaCompilationStatus.PENDING:
                raise PersonaCompilationConflictError("persona proposal was already decided")
            status = (
                PersonaCompilationStatus.APPROVED
                if parsed_decision == PersonaCompilationDecision.APPROVE
                else PersonaCompilationStatus.REJECTED
            )
        normalized_reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
        return replace(
            proposal,
            status=status,
            decided_by=actor_id,
            decided_at=decided_at or utc_now(),
            decision_reason=normalized_reason,
        )

    @classmethod
    def manifest_from_approved(
        cls,
        proposal: PersonaCompilationProposal,
    ) -> PersonaManifest:
        """Materializes the exact approved proposal revision as a Manifest."""
        if proposal.status != PersonaCompilationStatus.APPROVED:
            raise PersonaCompilationConflictError(
                "only an approved proposal can become a Persona Manifest"
            )
        if proposal.decided_by is None or proposal.decided_at is None:
            raise PersonaCompilationConflictError("approved proposal lacks decision provenance")
        manifest_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:persona-manifest:{proposal.blueprint_id}:"
                    f"{proposal.blueprint_revision}:{proposal.content_fingerprint}"
                ),
            )
        )
        return PersonaManifest(
            manifest_id=manifest_id,
            blueprint_id=proposal.blueprint_id,
            blueprint_revision=proposal.blueprint_revision,
            source_sha256=proposal.source_sha256,
            candidate=proposal.candidate,
            content_fingerprint=proposal.content_fingerprint,
            approved_proposal_id=proposal.proposal_id,
            approved_revision=proposal.revision,
            approved_by=proposal.decided_by,
            approved_at=proposal.decided_at,
        )

    @staticmethod
    def content_fingerprint(
        blueprint_id: str,
        blueprint_revision: int,
        source_sha256: str,
        candidate: PersonaManifestCandidate,
    ) -> str:
        """Returns the stable fingerprint used for idempotency and approval pinning."""
        payload = {
            "blueprint_id": blueprint_id,
            "blueprint_revision": blueprint_revision,
            "source_sha256": source_sha256,
            "candidate": candidate.model_dump(mode="json"),
        }
        return _sha256_text(_canonical_json(payload))

    @classmethod
    def _blueprint_identity(
        cls,
        blueprint: CharacterBlueprint,
    ) -> Tuple[str, int, str, str]:
        blueprint_id = cls._require_text(getattr(blueprint, "blueprint_id", ""), "blueprint_id")
        revision = int(getattr(blueprint, "revision", 1))
        if revision < 1:
            raise ValueError("blueprint revision must be positive")
        source_text = getattr(blueprint, "source_text", None)
        if not isinstance(source_text, str) or not source_text.strip():
            raise ValueError("Character Blueprint source_text must be a non-empty string")
        actual_sha256 = _sha256_text(source_text)
        declared_sha256 = getattr(blueprint, "source_sha256", None)
        if declared_sha256 and declared_sha256 != actual_sha256:
            raise ValueError("Character Blueprint source_sha256 does not match source_text")
        return blueprint_id, revision, source_text, actual_sha256

    @staticmethod
    def _validate_against_source(
        candidate: Union[PersonaManifestCandidate, Mapping[str, Any]],
        source_text: str,
    ) -> PersonaManifestCandidate:
        validated = PersonaManifestCandidate.model_validate(candidate)
        normalized_spans = []
        for span in validated.source_spans:
            if span.end > len(source_text):
                raise ValueError(f"source span {span.span_id!r} exceeds Character Blueprint length")
            exact_quote = source_text[span.start : span.end]
            if exact_quote != span.quote:
                raise ValueError(f"source span {span.span_id!r} quote does not match source text")
            quote_sha256 = _sha256_text(exact_quote)
            if span.quote_sha256 is not None and span.quote_sha256 != quote_sha256:
                raise ValueError(f"source span {span.span_id!r} quote_sha256 does not match")
            normalized_spans.append(span.model_copy(update={"quote_sha256": quote_sha256}))
        return validated.model_copy(update={"source_spans": normalized_spans})

    @staticmethod
    def _require_text(value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()


__all__ = ["PersonaCompiler"]

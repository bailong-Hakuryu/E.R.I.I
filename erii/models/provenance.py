"""Shared provenance value objects for derived E.R.I.I. artifacts."""

from dataclasses import dataclass, replace
from enum import Enum
import re
from typing import Any, Dict, Mapping, Optional, Union


_DESCRIPTOR_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _descriptor_part(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _DESCRIPTOR_PART.fullmatch(value.strip()):
        raise ValueError(
            f"{field_name} must be a non-empty, non-sensitive version identifier"
        )
    return value.strip()


class ArtifactProvenanceState(str, Enum):
    """Whether an artifact has complete modern provenance."""

    COMPLETE = "complete"
    LEGACY_UNAVAILABLE = "legacy_unavailable"


@dataclass(frozen=True)
class ExtractorDescriptor:
    """Non-sensitive identity of one versioned extraction capability."""

    extractor_id: str
    extractor_version: str
    extraction_schema_version: str = "1"
    erii_version: Optional[str] = None
    processed_at: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extractor_id",
            _descriptor_part(self.extractor_id, "extractor_id"),
        )
        object.__setattr__(
            self,
            "extractor_version",
            _descriptor_part(self.extractor_version, "extractor_version"),
        )
        object.__setattr__(
            self,
            "extraction_schema_version",
            _descriptor_part(
                self.extraction_schema_version,
                "extraction_schema_version",
            ),
        )
        if (self.erii_version is None) != (self.processed_at is None):
            raise ValueError(
                "erii_version and processed_at must either both be present or absent"
            )
        if self.erii_version is not None:
            object.__setattr__(
                self,
                "erii_version",
                _descriptor_part(self.erii_version, "erii_version"),
            )
            if not isinstance(self.processed_at, str) or not self.processed_at.strip():
                raise ValueError("processed_at must be a non-empty UTC timestamp")
            object.__setattr__(self, "processed_at", self.processed_at.strip())

    def for_processing(
        self,
        *,
        erii_version: str,
        processed_at: str,
    ) -> "ExtractorDescriptor":
        """Adds kernel-owned processing metadata to a host descriptor."""
        return replace(
            self,
            erii_version=erii_version,
            processed_at=processed_at,
        )

    def to_dict(self) -> Dict[str, Union[str, None]]:
        return {
            "extractor_id": self.extractor_id,
            "extractor_version": self.extractor_version,
            "extraction_schema_version": self.extraction_schema_version,
            "erii_version": self.erii_version,
            "processed_at": self.processed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExtractorDescriptor":
        required = {
            "extractor_id",
            "extractor_version",
            "extraction_schema_version",
        }
        allowed = required | {"erii_version", "processed_at"}
        if not required.issubset(data) or not set(data).issubset(allowed):
            raise ValueError("ExtractorDescriptor contains unknown or missing fields")
        return cls(
            extractor_id=data["extractor_id"],
            extractor_version=data["extractor_version"],
            extraction_schema_version=data["extraction_schema_version"],
            erii_version=data.get("erii_version"),
            processed_at=data.get("processed_at"),
        )

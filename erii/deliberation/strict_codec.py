"""Strict, deterministic JSON codec for Character Deliberation boundaries."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, get_args, get_origin

from pydantic import BaseModel


class StrictJSONDecoder(json.JSONDecoder):
    """JSON decoder that rejects duplicate object member names."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("object_pairs_hook", None)
        super().__init__(*args, object_pairs_hook=self._unique_object, **kwargs)

    @staticmethod
    def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key detected")
            result[key] = value
        return result


class StrictCanonicalCodec:
    """Canonical JSON serializer, parser, typed decoder, and SHA-256 helper."""

    MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
    MAX_NESTING_DEPTH = 32
    MAX_ARRAY_LENGTH = 10_000
    MAX_OBJECT_KEYS = 1_000
    MAX_INTEGER_DIGITS = 1_000

    @classmethod
    def serialize(cls, obj: Any) -> str:
        cls._validate_structure(obj)
        try:
            result = json.dumps(
                obj,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ValueError("unsupported or non-finite JSON value") from None
        cls._validate_document_size(result)
        return result

    @classmethod
    def deserialize(cls, json_str: str) -> Any:
        if type(json_str) is not str:
            raise TypeError("JSON document must be a string")
        cls._validate_document_size(json_str)

        public_error: ValueError | None = None
        data: Any = None
        try:
            data = json.loads(
                json_str,
                cls=StrictJSONDecoder,
                parse_constant=_reject_json_constant,
                parse_int=_parse_bounded_integer,
            )
        except ValueError as exc:
            message = str(exc)
            if "Duplicate JSON key" in message:
                public_error = ValueError("Duplicate JSON key detected")
            elif "Invalid JSON constant" in message:
                public_error = ValueError("Invalid JSON constant")
            elif "Integer literal" in message:
                public_error = ValueError("Integer literal exceeds maximum length")
            else:
                public_error = ValueError("Invalid JSON syntax")

        if public_error is not None:
            raise public_error

        cls._validate_structure(data)
        return data

    @classmethod
    def decode_as(cls, json_str: str, model_type: type[Any]) -> Any:
        public_error: ValueError | None = None
        data: Any = None
        try:
            data = cls.deserialize(json_str)
        except ValueError as exc:
            public_error = ValueError(_safe_deserialization_message(exc))

        if public_error is not None:
            raise public_error

        wire_error = _validate_wire_shape(data, model_type)
        if wire_error is not None:
            raise ValueError(wire_error)

        instance: Any = None
        public_error = None
        try:
            canonical = cls.serialize(data)
            instance = model_type.model_validate_json(canonical, strict=True)
        except Exception as exc:
            if hasattr(exc, "errors"):
                error_types = _safe_validation_error_types(exc.errors(include_input=False))
                public_error = ValueError(
                    f"Failed to validate schema {model_type.__name__}; types={error_types}"
                )
            else:
                public_error = ValueError(
                    f"Failed to validate schema {model_type.__name__}; types=validation_error"
                )

        if public_error is not None:
            raise public_error
        return instance

    @classmethod
    def fingerprint(cls, obj: Any, *, domain: str = "erii-canonical-json/v1") -> str:
        if not domain or not domain.isascii():
            raise ValueError("fingerprint domain must be non-empty ASCII")
        canonical = cls.serialize(obj).encode("utf-8")
        digest_input = domain.encode("ascii") + b"\x00" + canonical
        return hashlib.sha256(digest_input).hexdigest()

    @classmethod
    def verify_integrity(
        cls,
        obj: Any,
        expected_fingerprint: str,
        *,
        domain: str = "erii-canonical-json/v1",
    ) -> bool:
        return cls.fingerprint(obj, domain=domain) == expected_fingerprint

    @classmethod
    def _validate_document_size(cls, document: str) -> None:
        try:
            size = len(document.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("Invalid UTF-8 document") from None
        if size > cls.MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"Document size {size} bytes exceeds maximum {cls.MAX_DOCUMENT_BYTES} bytes"
            )

    @classmethod
    def _validate_structure(cls, obj: Any, depth: int = 0) -> None:
        if depth > cls.MAX_NESTING_DEPTH:
            raise ValueError(
                f"Nesting depth {depth} exceeds maximum {cls.MAX_NESTING_DEPTH}"
            )
        if obj is None or type(obj) is bool:
            return
        if type(obj) is int:
            if len(str(abs(obj))) > cls.MAX_INTEGER_DIGITS:
                raise ValueError("Integer literal exceeds maximum length")
            return
        if type(obj) is float:
            if not math.isfinite(obj):
                raise ValueError("non-finite number")
            return
        if type(obj) is str:
            cls._validate_string(obj)
            return
        if type(obj) is list:
            if len(obj) > cls.MAX_ARRAY_LENGTH:
                raise ValueError(
                    f"Array has {len(obj)} elements, exceeds maximum {cls.MAX_ARRAY_LENGTH}"
                )
            for item in obj:
                cls._validate_structure(item, depth + 1)
            return
        if type(obj) is dict:
            if len(obj) > cls.MAX_OBJECT_KEYS:
                raise ValueError(
                    f"Object has {len(obj)} keys, exceeds maximum {cls.MAX_OBJECT_KEYS}"
                )
            for key, value in obj.items():
                if type(key) is not str:
                    raise ValueError("object key must be a string")
                cls._validate_string(key)
                cls._validate_structure(value, depth + 1)
            return
        raise ValueError(f"unsupported JSON type: {type(obj).__name__}")

    @staticmethod
    def _validate_string(value: str) -> None:
        for char in value:
            code = ord(char)
            if code == 0:
                raise ValueError("String contains NUL character")
            if 1 <= code <= 0x1F and code not in (0x09, 0x0A, 0x0D):
                raise ValueError("String contains illegal control character")
            if 0xD800 <= code <= 0xDFFF:
                raise ValueError("String contains lone surrogate character")


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("Invalid JSON constant")


def _parse_bounded_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > StrictCanonicalCodec.MAX_INTEGER_DIGITS:
        raise ValueError("Integer literal exceeds maximum length")
    return int(value)


def _safe_deserialization_message(exc: ValueError) -> str:
    message = str(exc)
    if "Duplicate JSON key" in message:
        return "Duplicate JSON key detected"
    if "constant" in message:
        return "Invalid JSON constant"
    if "Document size" in message:
        return "JSON document exceeds size budget"
    if "Nesting depth" in message:
        return "JSON nesting depth exceeds budget"
    if "Array has" in message or "Object has" in message:
        return "JSON collection exceeds budget"
    if "UTF-8" in message or "surrogate" in message or "control" in message or "NUL" in message:
        return "Invalid JSON text"
    return "Invalid JSON syntax"


def _safe_validation_error_types(errors: list[dict[str, Any]]) -> str:
    safe: list[str] = []
    for error in errors:
        value = str(error.get("type", "validation_error"))
        if value.isascii() and all(char.isalnum() or char == "_" for char in value):
            safe.append(value)
        else:
            safe.append("validation_error")
    return ",".join(safe) or "validation_error"


def _validate_wire_shape(data: Any, model_type: type[Any]) -> str | None:
    if not isinstance(model_type, type) or not issubclass(model_type, BaseModel):
        return "Failed to validate schema; invalid model type"
    return _validate_model_wire_shape(data, model_type)


def _validate_model_wire_shape(data: Any, model_type: type[BaseModel]) -> str | None:
    if type(data) is not dict:
        return f"Failed to validate schema {model_type.__name__}; object required"
    required = getattr(model_type, "wire_required_fields", frozenset())
    if required.difference(data):
        return f"Failed to validate schema {model_type.__name__}; missing required fields"

    for name, field in model_type.model_fields.items():
        if name not in data:
            continue
        nested = _nested_model_types(field.annotation)
        if not nested:
            continue
        value = data[name]
        if get_origin(field.annotation) is tuple:
            if type(value) is not list:
                return f"Failed to validate schema {model_type.__name__}; array required"
            for item in value:
                error = _validate_model_wire_shape(item, nested[0])
                if error is not None:
                    return error
        elif type(value) is dict:
            error = _validate_model_wire_shape(value, nested[0])
            if error is not None:
                return error
    return None


def _nested_model_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    found: list[type[BaseModel]] = []
    for candidate in (annotation, *get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            found.append(candidate)
    return tuple(found)


def validated_model_copy(model: Any, *, update: dict[str, Any] | None = None) -> Any:
    if not isinstance(model, BaseModel):
        raise TypeError("model must be a Pydantic model")
    return model.model_copy(update={} if update is None else update, deep=True)


__all__ = ["StrictJSONDecoder", "StrictCanonicalCodec", "validated_model_copy"]

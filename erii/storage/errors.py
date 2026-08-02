"""Compatibility imports for storage errors now defined at package level."""

from erii.errors import StorageError, StorageIntegrityError, StorageWriteError


__all__ = ["StorageError", "StorageIntegrityError", "StorageWriteError"]

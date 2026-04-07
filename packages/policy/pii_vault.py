"""Encrypted PII vault.

Sensitive values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
and stored under a UUID key.  The key material comes from the
FORGECHAIN_VAULT_KEY env var (32 url-safe base64 bytes).

Usage:
    vault = PIIVault()
    ref_id = vault.store("john.doe@example.com")
    # pass ref_id around safely; never the raw value
    original = vault.retrieve(ref_id)
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

from cryptography.fernet import Fernet


def _load_key() -> bytes:
    raw = os.getenv("FORGECHAIN_VAULT_KEY")
    if not raw:
        raise EnvironmentError(
            "FORGECHAIN_VAULT_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return raw.encode()


class PIIPolicy:
    """Per-task PII handling policy."""

    def __init__(
        self,
        *,
        redact_before_prompt: bool = True,
        store_in_vault: bool = True,
        audit_log: bool = True,
    ) -> None:
        self.redact_before_prompt = redact_before_prompt
        self.store_in_vault = store_in_vault
        self.audit_log = audit_log

    @classmethod
    def strict(cls) -> "PIIPolicy":
        return cls(redact_before_prompt=True, store_in_vault=True, audit_log=True)

    @classmethod
    def permissive(cls) -> "PIIPolicy":
        return cls(redact_before_prompt=False, store_in_vault=False, audit_log=True)


class PIIVault:
    """In-memory encrypted vault.  For production, replace _store with
    a Redis hash or Postgres table backed by the same Fernet key."""

    def __init__(self) -> None:
        self._fernet = Fernet(_load_key())
        self._store: dict[str, bytes] = {}

    def store(self, value: str) -> str:
        """Encrypt *value*, store it, return an opaque reference ID."""
        ref_id = str(uuid.uuid4())
        self._store[ref_id] = self._fernet.encrypt(value.encode())
        return ref_id

    def retrieve(self, ref_id: str) -> Optional[str]:
        """Decrypt and return original value, or None if not found."""
        encrypted = self._store.get(ref_id)
        if encrypted is None:
            return None
        return self._fernet.decrypt(encrypted).decode()

    def delete(self, ref_id: str) -> None:
        self._store.pop(ref_id, None)

    def __len__(self) -> int:
        return len(self._store)

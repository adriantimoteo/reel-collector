import secrets
from typing import Protocol

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"  # lowercase base32 (a-z2-7)


class TokenSource(Protocol):
    def token(self, n: int) -> str: ...


class SecretsTokenSource:
    def token(self, n: int) -> str:
        return "".join(secrets.choice(_ALPHABET) for _ in range(n))

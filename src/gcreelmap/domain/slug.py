import re
import unicodedata

from gcreelmap.domain.tokens import TokenSource


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    truncated = collapsed[:24].rstrip("-")
    return truncated or "trip"


def new_slug(name: str, tokens: TokenSource) -> str:
    return f"{slugify(name)}-{tokens.token(10)}"

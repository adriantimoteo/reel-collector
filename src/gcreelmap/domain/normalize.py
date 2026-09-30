import unicodedata
from typing import Literal

from rapidfuzz import fuzz

ScriptKind = Literal["latin", "cjk", "hangul", "other", "mixed", "none"]


def normalize_name(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    recomposed = unicodedata.normalize("NFKC", without_marks)
    lowered = recomposed.casefold().replace("&", "and")
    spaced = "".join(ch if ch.isalnum() else " " for ch in lowered)
    return " ".join(spaced.split())


def _char_script(ch: str) -> str:
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "other"
    if name.startswith("HANGUL"):
        return "hangul"
    if name.startswith(("CJK", "HIRAGANA", "KATAKANA")):
        return "cjk"
    if name.startswith("LATIN"):
        return "latin"
    return "other"


def script_of(s: str) -> ScriptKind:
    found: set[str] = {_char_script(ch) for ch in s if ch.isalpha()}
    if not found:
        return "none"
    if len(found) > 1:
        return "mixed"
    only = next(iter(found))
    if only == "hangul":
        return "hangul"
    if only == "cjk":
        return "cjk"
    if only == "latin":
        return "latin"
    return "other"


def name_similarity(a: str, b: str) -> float:
    """Strict comparison, used for merging."""
    return fuzz.ratio(normalize_name(a), normalize_name(b)) / 100


def match_score(raw: str, candidate: str) -> float:
    """Loose comparison, used to judge a geocoder result against the extracted name."""
    return fuzz.token_set_ratio(normalize_name(raw), normalize_name(candidate)) / 100

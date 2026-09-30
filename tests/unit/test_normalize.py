import pytest

from gcreelmap.domain.normalize import match_score, name_similarity, normalize_name, script_of


def test_normalize_name_basic() -> None:
    assert normalize_name("Café  Nook & Co.") == "cafe nook and co"


def test_normalize_name_hangul_survives_intact() -> None:
    result = normalize_name("카페 노크")
    assert result == "카페 노크"


def test_normalize_name_fullwidth_latin() -> None:
    assert normalize_name("ＩＣＨＩＲＡＮ") == "ichiran"


def test_normalize_name_strips_punctuation_and_emoji() -> None:
    assert normalize_name("Ichiran!!! 🍜 (Shibuya)") == "ichiran shibuya"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ichiran", "latin"),
        ("一踮", "cjk"),
        ("이치란", "hangul"),
        ("Ichiran 一踮", "mixed"),
        ("123", "none"),
    ],
)
def test_script_of(text: str, expected: str) -> None:
    assert script_of(text) == expected


def test_name_similarity_ignores_case_and_spacing() -> None:
    assert name_similarity("Ichiran Shibuya", "ichiran  shibuya") == 1.0


def test_name_similarity_different_names_score_low() -> None:
    assert name_similarity("Cafe A", "Cafe B") < 0.85


def test_match_score_loose_substring() -> None:
    assert match_score("Ichiran", "Ichiran Shibuya") >= 0.9

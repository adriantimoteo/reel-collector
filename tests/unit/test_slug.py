import re

from gcreelmap.domain.slug import new_slug, slugify
from gcreelmap.domain.tokens import SecretsTokenSource
from tests.support.tokens import FakeTokenSource

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_basic_slugify() -> None:
    assert slugify("Tokyo Trip 2026!") == "tokyo-trip-2026"


def test_korean_only_name_falls_back_to_trip() -> None:
    assert slugify("서울 여행") == "trip"


def test_long_name_truncates_without_trailing_dash() -> None:
    slug = slugify("A Very Long Trip Name That Goes On And On And On")
    assert len(slug) <= 24
    assert not slug.endswith("-")


def test_accented_name() -> None:
    assert slugify("Café Kyōto") == "cafe-kyoto"


def test_new_slug_with_fake_token_source() -> None:
    tokens = FakeTokenSource()
    assert new_slug("Tokyo", tokens) == "tokyo-aaaaaaaaaa"


def test_slugs_always_match_regex() -> None:
    for name in ("Tokyo Trip 2026!", "서울 여행", "Café Kyōto", "", "   ", "a" * 50):
        assert _SLUG_RE.fullmatch(slugify(name))


def test_real_token_source_two_calls_differ() -> None:
    tokens = SecretsTokenSource()
    assert new_slug("Tokyo", tokens) != new_slug("Tokyo", tokens)

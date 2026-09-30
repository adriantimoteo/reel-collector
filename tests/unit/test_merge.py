import random
from dataclasses import replace

from gcreelmap.domain.merge import MentionRecord, MergeConfig, merge_mentions

_T = "2026-03-01T00:00:00Z"
_T2 = "2026-03-02T00:00:00Z"

# ~80m apart (well under the 150m merge radius)
_LAT_80M = 0.0007
# ~445m apart (well over the 150m merge radius)
_LAT_445M = 0.004


def _resolved(
    id_: int,
    reel_id: int,
    *,
    canonical_id: str,
    name: str,
    lat: float = 35.0,
    lng: float = 139.0,
    provider: str = "google",
    confidence: float = 0.9,
    match_score: float = 0.9,
    category: str | None = "food",
    blurb: str | None = "great place",
    address: str | None = "1 Main St",
    processed_at: str = _T,
) -> MentionRecord:
    return MentionRecord(
        id=id_,
        reel_id=reel_id,
        reel_processed_at=processed_at,
        kind="place",
        raw_name=name,
        raw_area=None,
        raw_category=category,
        raw_blurb=blurb,
        confidence=confidence,
        status="resolved",
        reason=None,
        match_score=match_score,
        resolved_provider=provider,
        resolved_canonical_id=canonical_id,
        resolved_name=name,
        resolved_address=address,
        resolved_lat=lat,
        resolved_lng=lng,
    )


def _unresolved(
    id_: int,
    reel_id: int,
    *,
    name: str,
    area: str | None = None,
    confidence: float = 0.6,
    reason: str = "geocode_failed",
    category: str | None = None,
    blurb: str | None = None,
    processed_at: str = _T,
) -> MentionRecord:
    return MentionRecord(
        id=id_,
        reel_id=reel_id,
        reel_processed_at=processed_at,
        kind="place",
        raw_name=name,
        raw_area=area,
        raw_category=category,
        raw_blurb=blurb,
        confidence=confidence,
        status="unresolved",
        reason=reason,
        match_score=None,
        resolved_provider=None,
        resolved_canonical_id=None,
        resolved_name=None,
        resolved_address=None,
        resolved_lat=None,
        resolved_lng=None,
    )


def _pending(id_: int, reel_id: int, name: str = "Whatever") -> MentionRecord:
    return MentionRecord(
        id=id_,
        reel_id=reel_id,
        reel_processed_at=_T,
        kind="place",
        raw_name=name,
        raw_area=None,
        raw_category=None,
        raw_blurb=None,
        confidence=0.9,
        status="pending",
        reason=None,
        match_score=None,
        resolved_provider=None,
        resolved_canonical_id=None,
        resolved_name=None,
        resolved_address=None,
        resolved_lat=None,
        resolved_lng=None,
    )


def _skipped(id_: int, reel_id: int, name: str = "A book") -> MentionRecord:
    return replace(_pending(id_, reel_id, name), status="skipped")


def test_same_canonical_id_two_reels_one_item() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="X", name="Ichiran"),
        _resolved(2, 20, canonical_id="X", name="Ichiran"),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    assert items[0].mention_count == 2


def test_same_reel_mentioning_twice_counts_once() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="X", name="Ichiran"),
        _resolved(2, 10, canonical_id="X", name="Ichiran"),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    assert items[0].mention_count == 1


def test_close_and_similar_names_merge() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran Shibuya", lat=35.0, lng=139.0),
        _resolved(
            2, 20, canonical_id="B", name="Ichiran Shibuya Main", lat=35.0 + _LAT_80M, lng=139.0
        ),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    assert items[0].mention_count == 2


def test_close_but_dissimilar_names_do_not_merge() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Cafe A", lat=35.0, lng=139.0),
        _resolved(2, 20, canonical_id="B", name="Cafe B", lat=35.0 + _LAT_80M, lng=139.0),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 2


def test_far_apart_identical_names_do_not_merge() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran", lat=35.0, lng=139.0),
        _resolved(2, 20, canonical_id="B", name="Ichiran", lat=35.0 + _LAT_445M, lng=139.0),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 2


def test_transitive_merge() -> None:
    # A~B directly, B~C directly; A~C need not pass directly, but all three
    # must end up in one item via transitivity.
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran Shibuya", lat=35.0, lng=139.0),
        _resolved(
            2, 20, canonical_id="B", name="Ichiran Shibuya Main", lat=35.0 + _LAT_80M, lng=139.0
        ),
        _resolved(
            3, 30, canonical_id="C", name="Ichiran Shibuya", lat=35.0 + 2 * _LAT_80M, lng=139.0
        ),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    assert items[0].mention_count == 3


def test_unresolved_same_name_area_two_reels_one_review_item() -> None:
    mentions = [
        _unresolved(1, 10, name="Nook Cafe", area="Bukchon"),
        _unresolved(2, 20, name="Nook Cafe", area="Bukchon"),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    item = items[0]
    assert item.mention_count == 2
    assert item.needs_review is True


def test_unresolved_never_merges_with_resolved() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Nook Cafe", lat=35.0, lng=139.0),
        _unresolved(2, 20, name="Nook Cafe", area=None),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 2


def test_pending_and_skipped_excluded() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran"),
        _pending(2, 20, "Something"),
        _skipped(3, 30, "A book"),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    all_member_ids = {m for item in items for m in item.member_mention_ids}
    assert all_member_ids == {1}


def test_reason_precedence_low_match_beats_geocode_failed() -> None:
    mentions = [
        _unresolved(1, 10, name="Nook Cafe", area="Bukchon", reason="geocode_failed"),
        _unresolved(2, 20, name="Nook Cafe", area="Bukchon", reason="low_match"),
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    assert items[0].review_reason == "low_match"


def test_low_confidence_resolved_item_flagged_but_keeps_coordinates() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran", confidence=0.4, lat=35.0, lng=139.0)
    ]
    items = merge_mentions(mentions)
    assert len(items) == 1
    item = items[0]
    assert item.needs_review is True
    assert item.review_reason == "low_confidence"
    assert item.lat == 35.0
    assert item.lng == 139.0


def test_category_by_summed_confidence_prefers_non_other() -> None:
    mentions = [
        _resolved(
            1,
            10,
            canonical_id="A",
            name="Ichiran",
            confidence=0.9,
            category="other",
            match_score=0.9,
        ),
        _resolved(
            2,
            20,
            canonical_id="A",
            name="Ichiran",
            confidence=0.5,
            category="food",
            match_score=0.5,
        ),
    ]
    # "other" has higher summed confidence (0.9) than "food" (0.5), but the
    # rule prefers "food" only when scores TIE -- here "other" genuinely wins
    # on raw sum, so this asserts the summed-confidence rule itself.
    items = merge_mentions(mentions)
    assert items[0].category == "other"


def test_category_prefers_non_other_on_tie() -> None:
    mentions = [
        _resolved(
            1,
            10,
            canonical_id="A",
            name="Ichiran",
            confidence=0.7,
            category="other",
            match_score=0.9,
        ),
        _resolved(
            2,
            20,
            canonical_id="A",
            name="Ichiran",
            confidence=0.7,
            category="food",
            match_score=0.5,
        ),
    ]
    items = merge_mentions(mentions)
    assert items[0].category == "food"


def test_blurb_from_highest_confidence_member() -> None:
    mentions = [
        _resolved(
            1, 10, canonical_id="A", name="Ichiran", confidence=0.5, blurb="ok", match_score=0.5
        ),
        _resolved(
            2,
            20,
            canonical_id="A",
            name="Ichiran",
            confidence=0.9,
            blurb="amazing",
            match_score=0.9,
        ),
    ]
    items = merge_mentions(mentions)
    assert items[0].blurb == "amazing"


def test_name_from_best_match_score_member() -> None:
    mentions = [
        _resolved(
            1,
            10,
            canonical_id="A",
            name="Ichiran (low match)",
            confidence=0.9,
            match_score=0.5,
        ),
        _resolved(
            2,
            20,
            canonical_id="A",
            name="Ichiran Shibuya",
            confidence=0.5,
            match_score=0.95,
        ),
    ]
    items = merge_mentions(mentions)
    assert items[0].name == "Ichiran Shibuya"


def test_output_is_order_independent() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran Shibuya", lat=35.0, lng=139.0),
        _resolved(
            2, 20, canonical_id="B", name="Ichiran Shibuya Main", lat=35.0 + _LAT_80M, lng=139.0
        ),
        _unresolved(3, 30, name="Nook Cafe", area="Bukchon"),
        _unresolved(4, 40, name="Nook Cafe", area="Bukchon"),
        _resolved(5, 50, canonical_id="C", name="Meiji Shrine", lat=10.0, lng=10.0),
    ]
    baseline = merge_mentions(mentions)

    shuffled = list(mentions)
    random.shuffle(shuffled)
    result = merge_mentions(shuffled)

    assert result == baseline


def test_every_mention_appears_in_exactly_one_item() -> None:
    mentions = [
        _resolved(1, 10, canonical_id="A", name="Ichiran"),
        _resolved(2, 20, canonical_id="A", name="Ichiran"),
        _unresolved(3, 30, name="Nook Cafe", area="Bukchon"),
        _unresolved(4, 40, name="Meiji", area=None),
    ]
    items = merge_mentions(mentions)
    seen: list[int] = []
    for item in items:
        seen.extend(item.member_mention_ids)
    assert sorted(seen) == [1, 2, 3, 4]


def test_merge_config_defaults() -> None:
    cfg = MergeConfig()
    assert cfg.merge_name_similarity == 0.85
    assert cfg.merge_radius_m == 150.0
    assert cfg.review_min_match_score == 0.6
    assert cfg.review_min_confidence == 0.5

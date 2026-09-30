from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from gcreelmap.domain.geo import LatLng, haversine_m
from gcreelmap.domain.normalize import name_similarity, normalize_name


@dataclass(frozen=True)
class MergeConfig:
    merge_name_similarity: float = 0.85
    merge_radius_m: float = 150.0
    review_min_match_score: float = 0.6
    review_min_confidence: float = 0.5


@dataclass(frozen=True)
class MentionRecord:
    id: int
    reel_id: int
    reel_processed_at: str
    kind: str
    raw_name: str
    raw_area: str | None
    raw_category: str | None
    raw_blurb: str | None
    confidence: float
    status: str  # resolution_status: pending | resolved | unresolved | skipped
    reason: str | None  # resolution_reason
    match_score: float | None
    resolved_provider: str | None
    resolved_canonical_id: str | None
    resolved_name: str | None
    resolved_address: str | None
    resolved_lat: float | None
    resolved_lng: float | None


@dataclass(frozen=True)
class MergedItem:
    merge_key: str
    candidate_keys: frozenset[str]
    canonical_id: str | None
    provider: str | None
    name: str
    address: str | None
    lat: float | None
    lng: float | None
    category: str | None
    blurb: str | None
    mention_count: int
    last_mentioned_at: str
    needs_review: bool
    review_reason: str | None
    member_mention_ids: tuple[int, ...]


class _UnionFind:
    def __init__(self, keys: Sequence[tuple[str, str]]) -> None:
        self._parent: dict[tuple[str, str], tuple[str, str]] = {k: k for k in keys}

    def find(self, k: tuple[str, str]) -> tuple[str, str]:
        while self._parent[k] != k:
            self._parent[k] = self._parent[self._parent[k]]
            k = self._parent[k]
        return k

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _best_resolved(members: Sequence[MentionRecord]) -> MentionRecord:
    return max(members, key=lambda m: (m.match_score or 0.0, m.confidence, -m.id))


def _pick_category(members: Sequence[MentionRecord]) -> str | None:
    sums: dict[str, float] = defaultdict(float)
    for m in members:
        if m.raw_category:
            sums[m.raw_category] += m.confidence
    if not sums:
        return None
    ranked = sorted(sums, key=lambda cat: (-sums[cat], 0 if cat != "other" else 1, cat))
    return ranked[0]


def _pick_blurb(members: Sequence[MentionRecord]) -> str | None:
    candidates = [m for m in members if m.raw_blurb]
    if not candidates:
        return None
    return max(candidates, key=lambda m: (m.confidence, m.id)).raw_blurb


def _pick_most_frequent_raw_name(members: Sequence[MentionRecord]) -> str:
    groups: dict[str, list[MentionRecord]] = defaultdict(list)
    for m in members:
        groups[m.raw_name].append(m)

    def key(name: str) -> tuple[int, float, int]:
        group = groups[name]
        best = max(group, key=lambda m: (m.confidence, -m.id))
        return (len(group), best.confidence, -best.id)

    return max(groups, key=key)


def _merge_resolved(members: Sequence[MentionRecord], cfg: MergeConfig) -> list[MergedItem]:
    groups: dict[tuple[str, str], list[MentionRecord]] = defaultdict(list)
    for m in members:
        groups[(m.resolved_provider or "", m.resolved_canonical_id or "")].append(m)

    keys = list(groups.keys())
    uf = _UnionFind(keys)
    reps = {k: _best_resolved(v) for k, v in groups.items()}

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ka, kb = keys[i], keys[j]
            ra, rb = reps[ka], reps[kb]
            if (
                ra.resolved_lat is None
                or ra.resolved_lng is None
                or rb.resolved_lat is None
                or rb.resolved_lng is None
            ):
                continue
            dist = haversine_m(
                LatLng(ra.resolved_lat, ra.resolved_lng),
                LatLng(rb.resolved_lat, rb.resolved_lng),
            )
            if dist > cfg.merge_radius_m:
                continue
            sim = name_similarity(ra.resolved_name or "", rb.resolved_name or "")
            if sim >= cfg.merge_name_similarity:
                uf.union(ka, kb)

    final: dict[tuple[str, str], list[MentionRecord]] = defaultdict(list)
    for k, group_members in groups.items():
        final[uf.find(k)].extend(group_members)

    items: list[MergedItem] = []
    for cluster_members in final.values():
        best = _best_resolved(cluster_members)
        candidate_keys = frozenset(
            f"id:{m.resolved_provider}:{m.resolved_canonical_id}" for m in cluster_members
        )
        max_confidence = max(m.confidence for m in cluster_members)
        needs_review = max_confidence < cfg.review_min_confidence
        items.append(
            MergedItem(
                merge_key=f"id:{best.resolved_provider}:{best.resolved_canonical_id}",
                candidate_keys=candidate_keys,
                canonical_id=best.resolved_canonical_id,
                provider=best.resolved_provider,
                name=best.resolved_name or "",
                address=best.resolved_address,
                lat=best.resolved_lat,
                lng=best.resolved_lng,
                category=_pick_category(cluster_members),
                blurb=_pick_blurb(cluster_members),
                mention_count=len({m.reel_id for m in cluster_members}),
                last_mentioned_at=max(m.reel_processed_at for m in cluster_members),
                needs_review=needs_review,
                review_reason="low_confidence" if needs_review else None,
                member_mention_ids=tuple(sorted(m.id for m in cluster_members)),
            )
        )
    return items


def _merge_unresolved(members: Sequence[MentionRecord]) -> list[MergedItem]:
    groups: dict[tuple[str, str], list[MentionRecord]] = defaultdict(list)
    for m in members:
        groups[(normalize_name(m.raw_name), normalize_name(m.raw_area or ""))].append(m)

    items: list[MergedItem] = []
    for (norm_name, norm_area), cluster_members in groups.items():
        name = _pick_most_frequent_raw_name(cluster_members)
        has_low_match = any(m.reason == "low_match" for m in cluster_members)
        items.append(
            MergedItem(
                merge_key=f"raw:{norm_name}|{norm_area}",
                candidate_keys=frozenset([f"raw:{norm_name}|{norm_area}"]),
                canonical_id=None,
                provider=None,
                name=name,
                address=None,
                lat=None,
                lng=None,
                category=_pick_category(cluster_members),
                blurb=_pick_blurb(cluster_members),
                mention_count=len({m.reel_id for m in cluster_members}),
                last_mentioned_at=max(m.reel_processed_at for m in cluster_members),
                needs_review=True,
                review_reason="low_match" if has_low_match else "geocode_failed",
                member_mention_ids=tuple(sorted(m.id for m in cluster_members)),
            )
        )
    return items


def merge_mentions(
    mentions: Sequence[MentionRecord], cfg: MergeConfig | None = None
) -> list[MergedItem]:
    cfg = cfg or MergeConfig()
    participating = sorted(
        (m for m in mentions if m.status in ("resolved", "unresolved")), key=lambda m: m.id
    )
    resolved = [m for m in participating if m.status == "resolved"]
    unresolved = [m for m in participating if m.status == "unresolved"]

    items = _merge_resolved(resolved, cfg) + _merge_unresolved(unresolved)
    return sorted(items, key=lambda it: it.merge_key)

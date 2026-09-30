import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from gcreelmap.domain.merge import MentionRecord, MergeConfig, MergedItem, merge_mentions
from gcreelmap.domain.timeutil import utc_iso
from gcreelmap.store.collections import bump_version
from gcreelmap.store.db import transaction


@dataclass(frozen=True)
class RebuildResult:
    items: list[MergedItem]
    changed: bool


def _load_mention_records(conn: sqlite3.Connection, collection_id: int) -> list[MentionRecord]:
    rows = conn.execute(
        "SELECT m.id, m.reel_id, r.processed_at, m.kind, m.raw_name, m.raw_area, "
        "m.raw_category, m.raw_blurb, m.confidence, m.resolution_status, m.resolution_reason, "
        "m.match_score, m.resolved_provider, m.resolved_canonical_id, m.resolved_name, "
        "m.resolved_address, m.resolved_lat, m.resolved_lng "
        "FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? AND r.status = 'done' "
        "ORDER BY m.id",
        (collection_id,),
    ).fetchall()
    return [
        MentionRecord(
            id=row[0],
            reel_id=row[1],
            reel_processed_at=row[2] or "",
            kind=row[3],
            raw_name=row[4],
            raw_area=row[5],
            raw_category=row[6],
            raw_blurb=row[7],
            confidence=row[8],
            status=row[9],
            reason=row[10],
            match_score=row[11],
            resolved_provider=row[12],
            resolved_canonical_id=row[13],
            resolved_name=row[14],
            resolved_address=row[15],
            resolved_lat=row[16],
            resolved_lng=row[17],
        )
        for row in rows
    ]


def _items_digest(conn: sqlite3.Connection, collection_id: int) -> str:
    item_rows = conn.execute(
        "SELECT kind, merge_key, canonical_id, provider, name, address, lat, lng, category, "
        "blurb, mention_count, last_mentioned_at, needs_review, review_reason "
        "FROM items WHERE collection_id = ? ORDER BY merge_key",
        (collection_id,),
    ).fetchall()
    mention_rows = conn.execute(
        "SELECT m.id, m.item_id FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ? ORDER BY m.id",
        (collection_id,),
    ).fetchall()
    parts = [repr(tuple(row)) for row in item_rows] + [repr(tuple(row)) for row in mention_rows]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _do_rebuild(
    conn: sqlite3.Connection, collection_id: int, cfg: MergeConfig, now: datetime
) -> list[MergedItem]:
    mention_records = _load_mention_records(conn, collection_id)
    merged_items = merge_mentions(mention_records, cfg)

    existing_rows = conn.execute(
        "SELECT id, merge_key FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchall()
    existing_by_key: dict[str, int] = {row[1]: row[0] for row in existing_rows}
    all_existing_ids = {row[0] for row in existing_rows}

    claimed_ids: set[int] = set()
    plan: list[tuple[MergedItem, int | None]] = []
    for merged in merged_items:
        candidate_ids = sorted(
            existing_by_key[k] for k in merged.candidate_keys if k in existing_by_key
        )
        target_id = next((cid for cid in candidate_ids if cid not in claimed_ids), None)
        if target_id is not None:
            claimed_ids.add(target_id)
        plan.append((merged, target_id))

    for item_id in all_existing_ids - claimed_ids:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    # Free the merge_key namespace among rows being reused first, so two kept rows
    # that effectively swap keys never collide with the UNIQUE(collection_id, merge_key)
    # constraint mid-rebuild.
    for _merged, target_id in plan:
        if target_id is not None:
            conn.execute(
                "UPDATE items SET merge_key = ? WHERE id = ?",
                (f"__pending__{target_id}", target_id),
            )

    now_text = utc_iso(now)
    mention_to_item: dict[int, int] = {}
    for merged, target_id in plan:
        if target_id is not None:
            conn.execute(
                "UPDATE items SET kind = 'place', merge_key = ?, canonical_id = ?, provider = ?, "
                "name = ?, address = ?, lat = ?, lng = ?, category = ?, blurb = ?, "
                "mention_count = ?, last_mentioned_at = ?, needs_review = ?, review_reason = ? "
                "WHERE id = ?",
                (
                    merged.merge_key,
                    merged.canonical_id,
                    merged.provider,
                    merged.name,
                    merged.address,
                    merged.lat,
                    merged.lng,
                    merged.category,
                    merged.blurb,
                    merged.mention_count,
                    merged.last_mentioned_at,
                    1 if merged.needs_review else 0,
                    merged.review_reason,
                    target_id,
                ),
            )
            item_id = target_id
        else:
            cur = conn.execute(
                "INSERT INTO items (collection_id, kind, merge_key, canonical_id, provider, "
                "name, address, lat, lng, category, blurb, mention_count, last_mentioned_at, "
                "needs_review, review_reason, created_at) "
                "VALUES (?, 'place', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    collection_id,
                    merged.merge_key,
                    merged.canonical_id,
                    merged.provider,
                    merged.name,
                    merged.address,
                    merged.lat,
                    merged.lng,
                    merged.category,
                    merged.blurb,
                    merged.mention_count,
                    merged.last_mentioned_at,
                    1 if merged.needs_review else 0,
                    merged.review_reason,
                    now_text,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("INSERT did not report a lastrowid")
            item_id = cur.lastrowid

        for mention_id in merged.member_mention_ids:
            mention_to_item[mention_id] = item_id

    all_mention_rows = conn.execute(
        "SELECT m.id FROM item_mentions m JOIN reels r ON r.id = m.reel_id "
        "WHERE r.collection_id = ?",
        (collection_id,),
    ).fetchall()
    for (mention_id,) in all_mention_rows:
        conn.execute(
            "UPDATE item_mentions SET item_id = ? WHERE id = ?",
            (mention_to_item.get(mention_id), mention_id),
        )

    return merged_items


def rebuild_items(
    conn: sqlite3.Connection,
    collection_id: int,
    *,
    cfg: MergeConfig | None = None,
    now: datetime,
) -> RebuildResult:
    """Rebuilds `items` for a collection from `item_mentions` (A4: item_mentions is
    truth, items is derived). Idempotent; the only writer of `items`."""
    cfg = cfg or MergeConfig()
    if conn.in_transaction:
        before_digest = _items_digest(conn, collection_id)
        merged_items = _do_rebuild(conn, collection_id, cfg, now)
        after_digest = _items_digest(conn, collection_id)
        changed = before_digest != after_digest
        if changed:
            bump_version(conn, collection_id)
    else:
        with transaction(conn):
            before_digest = _items_digest(conn, collection_id)
            merged_items = _do_rebuild(conn, collection_id, cfg, now)
            after_digest = _items_digest(conn, collection_id)
            changed = before_digest != after_digest
            if changed:
                bump_version(conn, collection_id)

    return RebuildResult(items=merged_items, changed=changed)

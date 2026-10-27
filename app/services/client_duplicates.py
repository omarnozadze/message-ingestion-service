from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from app.services.matching.client_query import canonical_search_text, outlet_tokens


class ClientLike(Protocol):
    id: object
    one_c_ref: str | None
    code: str | None
    display_name: str
    normalized_name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class DuplicateClientGroup:
    kind: str
    key: str
    client_ids: tuple[str, ...]
    display_names: tuple[str, ...]


def _clean(value: object) -> str:
    return str(value or "").strip().casefold()


def _branch_identity(value: str) -> tuple[str, str] | None:
    canonical = canonical_search_text(value)
    tokens = canonical.split()
    if not tokens:
        return None
    outlet_set = set(outlet_tokens(canonical))
    if not outlet_set:
        return None

    for idx, token in enumerate(tokens):
        if token not in outlet_set:
            continue
        prefix = " ".join(tokens[:idx]).strip()
        # A branch number without a chain/name prefix is too generic to classify
        # as a duplicate identity.
        if not prefix:
            return None
        return prefix, token
    return None


def detect_duplicate_clients(clients: Iterable[ClientLike]) -> tuple[DuplicateClientGroup, ...]:
    active = [item for item in clients if bool(item.is_active)]
    buckets: dict[tuple[str, str], list[ClientLike]] = {}

    for item in active:
        ref = _clean(item.one_c_ref)
        if ref:
            buckets.setdefault(("one_c_ref", ref), []).append(item)

        code = _clean(item.code)
        if code:
            buckets.setdefault(("catalog_code", code), []).append(item)

        normalized_name = canonical_search_text(item.normalized_name or item.display_name)
        if normalized_name:
            buckets.setdefault(("normalized_name", normalized_name), []).append(item)

        branch = _branch_identity(item.display_name)
        if branch is not None:
            chain, branch_code = branch
            buckets.setdefault(("branch_identity", f"{chain}|{branch_code}"), []).append(item)

    groups: list[DuplicateClientGroup] = []
    seen_members: set[tuple[str, tuple[str, ...]]] = set()
    for (kind, key), items in buckets.items():
        unique_by_id = {str(item.id): item for item in items}
        if len(unique_by_id) < 2:
            continue
        ids = tuple(sorted(unique_by_id))
        # Avoid repeating the same client set for a weaker identity when the
        # stronger DB identifiers already caught it. Keep different kinds only
        # if they identify a genuinely different set.
        marker = (kind, ids)
        if marker in seen_members:
            continue
        seen_members.add(marker)
        ordered = [unique_by_id[item_id] for item_id in ids]
        groups.append(
            DuplicateClientGroup(
                kind=kind,
                key=key,
                client_ids=ids,
                display_names=tuple(item.display_name for item in ordered),
            )
        )

    groups.sort(key=lambda item: (item.kind, item.key, item.client_ids))
    return tuple(groups)


def duplicate_client_count(groups: Iterable[DuplicateClientGroup]) -> int:
    ids: set[str] = set()
    for group in groups:
        ids.update(group.client_ids)
    return len(ids)

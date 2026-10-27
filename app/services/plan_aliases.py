from __future__ import annotations

import re

from app.services.matching.normalizer import normalize_text

ADDRESS_ALIAS_PREFIX = "addr "
MAX_ALIAS_LENGTH = 250

# Human-facing outlet/store markers seen in 1C visit-plan addresses, e.g.
#   "გვირილა N0203 დიღმის ..."
#   "რითეილ ჯგუფი * S224 - თბილისი ..."
# Messages usually omit the letter and use only 0203 / 224.
#
# N/S is REQUIRED for deriving an outlet-number alias. Ordinary street/building
# numbers are kept only inside a typed address alias and are never treated as
# outlet numbers by the matcher.
_STORE_MARKER = re.compile(
    r"(?i)(?:^|[\s*#№,;:\-])(?P<prefix>[ns])\s*(?P<digits>\d{2,6})(?=$|[\s,;:\-])"
)


def make_address_alias(address: str | None) -> str | None:
    if not address:
        return None
    normalized = normalize_text(" ".join(address.strip().split()))
    if not normalized:
        return None
    payload = normalized[: MAX_ALIAS_LENGTH - len(ADDRESS_ALIAS_PREFIX)].strip()
    if not payload:
        return None
    return f"{ADDRESS_ALIAS_PREFIX}{payload}"


def is_address_alias(value: str) -> bool:
    return normalize_text(value).startswith(ADDRESS_ALIAS_PREFIX)


def address_alias_text(value: str) -> str:
    normalized = normalize_text(value)
    if normalized.startswith(ADDRESS_ALIAS_PREFIX):
        return normalized[len(ADDRESS_ALIAS_PREFIX) :].strip()
    return normalized


def extract_plan_address_aliases(address: str | None) -> tuple[str, ...]:
    if not address:
        return ()

    raw = " ".join(address.strip().split())
    if not raw:
        return ()

    aliases: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = normalize_text(value)
        if normalized and normalized not in aliases:
            aliases.append(normalized)

    match = _STORE_MARKER.search(raw)
    if match is not None:
        digits = match.group("digits")
        marker_prefix = match.group("prefix").casefold()
        label = raw[: match.start()].strip(" \t-*#№,;:")
        label_norm = normalize_text(label)
        if label_norm:
            add(f"{label_norm} {digits}")
            add(f"{label_norm} {marker_prefix}{digits}")

    # Always index the complete human-readable address as a typed alias. This
    # makes searches such as "დიღმის მასივი კვარტალი 2" or "ლიბანის 16"
    # possible without confusing a building number with an outlet number.
    add(make_address_alias(raw))

    return tuple(aliases)

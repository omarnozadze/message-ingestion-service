from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.services.matching.normalizer import normalize_text


class VisitOperation(StrEnum):
    SET = "set"
    ADD = "add"
    MOVE = "move"
    REMOVE = "remove"
    REMOVE_ALL = "remove_all"


@dataclass(frozen=True, slots=True)
class VisitInstruction:
    agent_code: str
    agent_description: str
    target_days: tuple[int, ...]
    operation: VisitOperation
    source_day: int | None = None

    @property
    def target_day(self) -> int:
        """Backward-compatible single target: the last requested weekday."""
        return self.target_days[-1]

    @property
    def target_field(self) -> str:
        return f"Д{self.target_day}"

    @property
    def target_fields(self) -> tuple[str, ...]:
        return tuple(f"Д{day}" for day in self.target_days)


class VisitInstructionParseError(ValueError):
    pass


# Logical weekday numbers used by the parser: Monday=1 .. Sunday=7.
# The 1C Д1..Д7 fields are cycle slots anchored to the document Date and are
# translated in the OData integration before reads/writes.
_DAY_ALIASES: dict[int, tuple[str, ...]] = {
    1: ("ორშაბათი", "ორშაბათს", "ორშაბათზე", "ორშაბათიდან", "ორშაბათზეც"),
    2: ("სამშაბათი", "სამშაბათს", "სამშაბათზე", "სამშაბათიდან", "სამშაბათზეც"),
    3: ("ოთხშაბათი", "ოთხშაბათს", "ოთხშაბათზე", "ოთხშაბათიდან", "ოთხშაბათზეც"),
    4: ("ხუთშაბათი", "ხუთშაბათს", "ხუთშაბათზე", "ხუთშაბათიდან", "ხუთშაბათზეც"),
    5: ("პარასკევი", "პარასკევს", "პარასკევზე", "პარასკევიდან", "პარასკევზეც"),
    6: ("შაბათი", "შაბათს", "შაბათზე", "შაბათიდან", "შაბათზეც"),
    7: ("კვირა", "კვირას", "კვირაზე", "კვირიდან", "კვირაზეც"),
}

_TODAY_ALIASES = {
    "დღეს",
    "დღევანდელი",
    "დღევანდელ",
    "დღევანდელზე",
    "დღევანდელს",
    "დღევანდელზეც",
    "დღევანდელიდან",
}

_ADD_MARKERS = (
    "ჩასვი",
    "ჩასვით",
    "ჩასმა",
    "ჩამისვი",
    "ჩამიმატ",
    "დაამატ",
    "დამიმატ",
    "დამატებ",
    "დამატებით",
    "კიდევ",
    "ზეც",
    "დასვი",
    "მონიშნ",
)
_MOVE_MARKERS = ("გადაიტან", "გადმოიტან", "შეცვალ")
_REMOVE_MARKERS = (
    "ამოღებ",
    "ამოიღ",
    "ამომიღ",
    "მომიღ",
    "მოხს",
    "ამოშალ",
    "ამომიშალ",
    "მომიშალ",
    "ამომშალ",
    "წაშალ",
)
_ONLY_MARKER = "მხოლოდ"
_SET_MARKERS = ("იყოს", "იჯდეს", _ONLY_MARKER)


def requested_days_from_text(raw_text: str, *, business_date: date) -> tuple[int, ...]:
    normalized = normalize_text(raw_text)
    day_mentions = _find_day_mentions(normalized, business_date=business_date)
    return tuple(_unique_days(day for _, day in day_mentions))


def parse_visit_instruction(
    raw_text: str,
    *,
    agent_code: str,
    business_date: date,
    agent_prefix: str = "PS",
) -> VisitInstruction:
    normalized = normalize_text(raw_text)
    day_mentions = _find_day_mentions(normalized, business_date=business_date)

    # "გეგმიდან ამომიშალე/ამოიღე/წაშალე" means remove the client from
    # every visit slot in the requesting agent's plan; no weekday is required.
    if (
        not day_mentions
        and "გეგმიდან" in normalized
        and any(marker in normalized for marker in _REMOVE_MARKERS)
    ):
        return VisitInstruction(
            agent_code=agent_code,
            agent_description=f"{agent_prefix}{agent_code}",
            target_days=(),
            operation=VisitOperation.REMOVE_ALL,
            source_day=None,
        )

    if not day_mentions:
        # Human shorthand: "ჩასვი/ჩამისვი/დაამატე" without a named weekday
        # means add the client to today's visit slot.
        if any(marker in normalized for marker in _ADD_MARKERS):
            return VisitInstruction(
                agent_code=agent_code,
                agent_description=f"{agent_prefix}{agent_code}",
                target_days=(business_date.isoweekday(),),
                operation=VisitOperation.ADD,
                source_day=None,
            )

        raise VisitInstructionParseError("weekday/today was not found in the message")

    unique_days = _unique_days(day for _, day in day_mentions)
    source_day: int | None = None

    has_remove = any(marker in normalized for marker in _REMOVE_MARKERS)
    has_add = any(marker in normalized for marker in _ADD_MARKERS)

    if any(marker in normalized for marker in _MOVE_MARKERS):
        operation = VisitOperation.MOVE
        if len(unique_days) >= 2:
            source_day = unique_days[0]
            target_days = tuple(unique_days[1:])
        else:
            target_days = tuple(unique_days)
    elif has_remove and has_add and len(unique_days) >= 2:
        # "დღევანდელიდან ამოიღე და ოთხშაბათს ჩასვი" is a move, not a removal
        # from both days. Only the unambiguous two-day form is accepted.
        if len(unique_days) != 2:
            raise VisitInstructionParseError(
                "remove + add with more than two weekdays is ambiguous"
            )
        operation = VisitOperation.MOVE
        source_day = _source_day(day_mentions, normalized) or unique_days[0]
        target_days = tuple(day for day in unique_days if day != source_day)
    elif has_remove:
        operation = VisitOperation.REMOVE
        target_days = tuple(unique_days)
    elif has_add:
        # "მხოლოდ <day> ჩასვი" is a replacement schedule: the requested days
        # stay, every other visit day is cleared.
        operation = VisitOperation.SET if _ONLY_MARKER in normalized else VisitOperation.ADD
        target_days = tuple(unique_days)
    elif any(marker in normalized for marker in _SET_MARKERS):
        operation = VisitOperation.SET
        target_days = tuple(unique_days)
    else:
        # Compact messages with only a weekday are interpreted as SET. Explicit
        # "ჩასვი/ჩამისვი" commands above are ADD and preserve existing days.
        operation = VisitOperation.SET
        target_days = tuple(unique_days)

    if not target_days:
        raise VisitInstructionParseError("target weekday was not found in the message")

    return VisitInstruction(
        agent_code=agent_code,
        agent_description=f"{agent_prefix}{agent_code}",
        target_days=target_days,
        operation=operation,
        source_day=source_day,
    )


def _find_day_mentions(normalized: str, *, business_date: date) -> list[tuple[int, int]]:
    alias_to_day = {alias: day for day, aliases in _DAY_ALIASES.items() for alias in aliases}
    today_day = business_date.weekday() + 1
    matches: list[tuple[int, int]] = []
    for index, token in enumerate(normalized.split()):
        if token in _TODAY_ALIASES:
            matches.append((index, today_day))
            continue
        day = alias_to_day.get(token)
        if day is not None:
            matches.append((index, day))
    return matches


def _source_day(day_mentions: list[tuple[int, int]], normalized: str) -> int | None:
    """The weekday written with the "-იდან" (from) suffix, if exactly one."""
    tokens = normalized.split()
    sources = {day for index, day in day_mentions if tokens[index].endswith("იდან")}
    return next(iter(sources)) if len(sources) == 1 else None


def _unique_days(days) -> list[int]:
    result: list[int] = []
    for day in days:
        if day not in result:
            result.append(day)
    return result

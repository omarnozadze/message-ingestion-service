from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from app.services.matching.normalizer import normalize_text
from app.services.visit_instruction import (
    VisitInstruction,
    VisitInstructionParseError,
    VisitOperation,
    parse_visit_instruction,
    requested_days_from_text,
)


class MultiVisitRequestParseError(VisitInstructionParseError):
    pass


@dataclass(frozen=True, slots=True)
class ClientVisitAction:
    client_text: str
    instruction: VisitInstruction


@dataclass(frozen=True, slots=True)
class VisitRequest:
    actions: tuple[ClientVisitAction, ...]
    is_multi: bool


@dataclass(frozen=True, slots=True)
class _Marker:
    operation: VisitOperation
    style: str  # "leading" or "trailing"
    start: int
    end: int
    text: str


# Noun-like operation words usually come AFTER the client list.
_TRAILING_MARKERS: tuple[tuple[VisitOperation, str], ...] = (
    (VisitOperation.REMOVE, "ამოღება"),
    (VisitOperation.REMOVE, "მოხსნა"),
    (VisitOperation.REMOVE, "ამოშლა"),
    (VisitOperation.REMOVE, "წაშლა"),
    (VisitOperation.ADD, "ჩასმა"),
    (VisitOperation.ADD, "დამატება"),
)

# Imperative operation words usually come BEFORE the client list.
_LEADING_MARKERS: tuple[tuple[VisitOperation, str], ...] = (
    (VisitOperation.REMOVE, "ამოიღე"),
    (VisitOperation.REMOVE, "ამოიღეთ"),
    (VisitOperation.REMOVE, "მოხსენი"),
    (VisitOperation.REMOVE, "მოხსენით"),
    (VisitOperation.REMOVE, "ამოშალე"),
    (VisitOperation.REMOVE, "ამოშალეთ"),
    (VisitOperation.REMOVE, "წაშალე"),
    (VisitOperation.REMOVE, "წაშალეთ"),
    (VisitOperation.ADD, "ჩასვი"),
    (VisitOperation.ADD, "ჩასვით"),
    (VisitOperation.ADD, "ჩამისვი"),
    (VisitOperation.ADD, "ჩამიმატე"),
    (VisitOperation.ADD, "დაამატე"),
    (VisitOperation.ADD, "დაამატეთ"),
)

_REPLACEMENT_PREFIXES = (
    "მათნაცვლად",
    "მათ ნაცვლად",
    "ამისნაცვლად",
    "ამის ნაცვლად",
    "ნაცვლად",
)

_LEADING_FILLERS = {
    "და",
    "ასევე",
    "ხოლო",
    "მათნაცვლად",
    "ამისნაცვლად",
    "ნაცვლად",
    "მათ",
    "ამის",
}

_TRAILING_FILLERS = {
    "გვინდა",
    "გვსურს",
    "გთხოვ",
    "გთხოვთ",
    "უნდა",
    "მინდა",
    "მჭირდება",
    "გაკეთება",
}

# Extra words belonging to the global day scope, not to a client identity.
_SCOPE_FILLERS = {
    "მხოლოდ",
    "დღეზე",
    "დღეს",
    "დღისთვის",
    "დღის",
    "დღეში",
}


_DAY_WORD_RE = re.compile(
    r"(?:ორშაბათ(?:ი|ს|ზე|იდან|ზეც)?|"
    r"სამშაბათ(?:ი|ს|ზე|იდან|ზეც)?|"
    r"ოთხშაბათ(?:ი|ს|ზე|იდან|ზეც)?|"
    r"ხუთშაბათ(?:ი|ს|ზე|იდან|ზეც)?|"
    r"პარასკევ(?:ი|ს|ზე|იდან|ზეც)?|"
    r"შაბათ(?:ი|ს|ზე|იდან|ზეც)?|"
    r"კვირ(?:ა|ას|აზე|იდან|აზეც)?|"
    r"დღეს|დღევანდელი|დღევანდელ(?:ზე|ს|ზეც)?)"
)


def parse_visit_request(
    raw_text: str,
    *,
    agent_code: str,
    business_date: date,
    agent_prefix: str = "PS",
) -> VisitRequest:
    """Parse either one normal visit instruction or a multi-client request.

    Multi-client messages are intentionally conservative. All client phrases are
    extracted first; callers can resolve every client before touching 1C, which
    prevents partial writes.

    Example:
      "მხოლოდ დღევანდელ დღეზე ფრიდონ ხალვაშის 94 ორი ნაბიჯის და
       ფრიდონ ხალვაშის 101გ ორი ნაბიჯების ამოღება და მათნაცვლად
       ნიკორა ტბელ აბუსერიძის 45 ის ჩასმა გვინდა"

    becomes three actions: REMOVE, REMOVE, ADD on today's weekday.
    """

    normalized = normalize_text(raw_text)
    if not normalized:
        raise MultiVisitRequestParseError("message is empty")

    # Compact form: several "name + branch code" clients followed by ONE shared
    # instruction, e.g. "გვირილა 0231, რითეილ 2322 და რითეილ K223 ჩასვი სამშაბათზე".
    shared_clients = _split_shared_instruction_clients(raw_text)
    if shared_clients is not None:
        instruction = parse_visit_instruction(
            raw_text,
            agent_code=agent_code,
            business_date=business_date,
            agent_prefix=agent_prefix,
        )
        return VisitRequest(
            actions=tuple(
                ClientVisitAction(client_text=client, instruction=instruction)
                for client in shared_clients
            ),
            is_multi=True,
        )

    target_days = requested_days_from_text(normalized, business_date=business_date)
    if not target_days:
        raise MultiVisitRequestParseError("weekday/today was not found in the message")

    markers = _find_operation_markers(normalized)
    multi_actions = _extract_multi_actions(
        normalized,
        markers=markers,
        target_days=target_days,
        agent_code=agent_code,
        agent_prefix=agent_prefix,
    )
    if len(multi_actions) >= 2:
        return VisitRequest(actions=tuple(multi_actions), is_multi=True)

    # Preserve existing single-client behavior for the common compact form.
    instruction = parse_visit_instruction(
        raw_text,
        agent_code=agent_code,
        business_date=business_date,
        agent_prefix=agent_prefix,
    )
    return VisitRequest(
        actions=(ClientVisitAction(client_text=raw_text, instruction=instruction),),
        is_multi=False,
    )


def _find_operation_markers(text: str) -> list[_Marker]:
    found: list[_Marker] = []

    def add(operation: VisitOperation, marker_text: str, style: str) -> None:
        pattern = re.compile(rf"(?<!\w){re.escape(marker_text)}(?!\w)")
        for match in pattern.finditer(text):
            found.append(
                _Marker(
                    operation=operation,
                    style=style,
                    start=match.start(),
                    end=match.end(),
                    text=marker_text,
                )
            )

    for operation, marker in _TRAILING_MARKERS:
        add(operation, marker, "trailing")
    for operation, marker in _LEADING_MARKERS:
        add(operation, marker, "leading")

    # Prefer the longer marker if two would ever overlap.
    found.sort(key=lambda item: (item.start, -(item.end - item.start)))
    deduped: list[_Marker] = []
    for item in found:
        if deduped and item.start < deduped[-1].end:
            continue
        deduped.append(item)
    return deduped


def _extract_multi_actions(
    text: str,
    *,
    markers: list[_Marker],
    target_days: tuple[int, ...],
    agent_code: str,
    agent_prefix: str,
) -> list[ClientVisitAction]:
    # One marker can still refer to several clients ("A და B ამოღება"), but we
    # only switch to multi mode when extraction produces 2+ client actions.
    if not markers:
        return []

    actions: list[ClientVisitAction] = []
    for index, marker in enumerate(markers):
        prev_marker = markers[index - 1] if index > 0 else None
        next_marker = markers[index + 1] if index + 1 < len(markers) else None

        if marker.style == "leading":
            start = marker.end
            end = next_marker.start if next_marker is not None else len(text)
            segment = text[start:end]
        else:
            start = prev_marker.end if prev_marker is not None else 0
            end = marker.start
            segment = text[start:end]

        segment = _clean_segment(
            segment,
            is_first=(index == 0),
            is_last=(index == len(markers) - 1),
        )
        if not segment:
            continue

        client_parts = _split_client_list(segment)
        for client_text in client_parts:
            cleaned = _clean_client_phrase(client_text)
            if not _looks_like_client_phrase(cleaned):
                continue
            instruction = VisitInstruction(
                agent_code=agent_code,
                agent_description=f"{agent_prefix}{agent_code}",
                target_days=target_days,
                operation=marker.operation,
                source_day=None,
            )
            actions.append(
                ClientVisitAction(client_text=cleaned, instruction=instruction)
            )

    return _dedupe_actions(actions)


def _clean_segment(segment: str, *, is_first: bool, is_last: bool) -> str:
    value = normalize_text(segment)
    if not value:
        return ""

    if is_first:
        # The first operation's trailing segment often contains conversational
        # lead-in. Keep only the text after the final explicit day mention.
        matches = list(_DAY_WORD_RE.finditer(value))
        if matches:
            value = value[matches[-1].end() :].strip()

    # Replacement connectors belong to the operation grammar, not the client.
    for prefix in _REPLACEMENT_PREFIXES:
        if value.startswith(prefix + " "):
            value = value[len(prefix) :].strip()
            break

    tokens = value.split()
    while tokens and tokens[0] in _LEADING_FILLERS:
        tokens.pop(0)
    while tokens and tokens[0] in _SCOPE_FILLERS:
        tokens.pop(0)
    while tokens and (tokens[-1] in _TRAILING_FILLERS or tokens[-1] in {"და"}):
        tokens.pop()

    # "45 ის" is a common conversational spelling of possessive "45-ის".
    if len(tokens) >= 2 and tokens[-1] == "ის" and any(ch.isdigit() for ch in tokens[-2]):
        tokens.pop()

    return " ".join(tokens).strip()


def _split_client_list(segment: str) -> list[str]:
    # Punctuation has already been normalized to spaces. Split on list-style
    # conjunctions only when every resulting side still looks like a useful
    # identity; otherwise keep the original phrase intact.
    raw_parts = re.split(r"\s+(?:და|ასევე)\s+", segment)
    parts = [_clean_client_phrase(part) for part in raw_parts if part.strip()]
    if len(parts) <= 1:
        return parts
    if all(_looks_like_client_phrase(part) for part in parts):
        return parts
    return [segment]


def _clean_client_phrase(value: str) -> str:
    tokens = normalize_text(value).split()
    while tokens and tokens[0] in _LEADING_FILLERS:
        tokens.pop(0)
    while tokens and tokens[-1] in _TRAILING_FILLERS:
        tokens.pop()
    if len(tokens) >= 2 and tokens[-1] == "ის" and any(ch.isdigit() for ch in tokens[-2]):
        tokens.pop()
    return " ".join(tokens).strip()


def _looks_like_client_phrase(value: str) -> bool:
    tokens = normalize_text(value).split()
    if not tokens:
        return False
    if any(any(ch.isdigit() for ch in token) for token in tokens):
        return True
    # Name + locality/branch without a number can still be valid, but require
    # at least two words so a generic connector cannot become a client.
    return len(tokens) >= 2


def _dedupe_actions(actions: list[ClientVisitAction]) -> list[ClientVisitAction]:
    result: list[ClientVisitAction] = []
    seen: set[tuple[str, VisitOperation, tuple[int, ...]]] = set()
    for action in actions:
        key = (
            normalize_text(action.client_text),
            action.instruction.operation,
            action.instruction.target_days,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


# --- Shared-instruction client lists -------------------------------------------

# Any token starting with one of these stems belongs to the instruction, not to
# the client list that precedes it.
_INSTRUCTION_STEMS = (
    "ორშაბათ", "სამშაბათ", "ოთხშაბათ", "ხუთშაბათ", "პარასკევ", "შაბათ", "კვირ",
    "დღეს", "დღევანდელ",
    "მხოლოდ", "იყოს", "იჯდეს",
    "ჩასვი", "ჩასვით", "ჩასმა", "ჩამისვი", "ჩამიმატ", "დაამატ", "დამიმატ", "დამატებ",
    "დასვი", "მონიშნ",
    "გადაიტან", "გადმოიტან", "შეცვალ",
    "ამოღებ", "ამოიღ", "ამომიღ", "მომიღ", "მოხს", "ამოშალ", "ამომიშალ", "მომიშალ",
    "ამომშალ", "წაშალ", "გეგმიდან",
)
_TOKEN_PUNCTUATION = " \t\r\n,;:|.!?()[]{}\"'„“”«»"
_MAX_TOKENS_PER_CLIENT = 5


def _strip_token(token: str) -> str:
    return token.strip(_TOKEN_PUNCTUATION).casefold()


def _is_instruction_token(token: str) -> bool:
    value = _strip_token(token)
    return bool(value) and value.startswith(_INSTRUCTION_STEMS)


def _is_branch_code(token: str) -> bool:
    """Compact human outlet codes: 0133, #0296, K223, S225, 101გ, 3.2."""
    value = _strip_token(token).lstrip("#*")
    if not value or not any(ch.isdigit() for ch in value):
        return False
    return len(value) <= 10 and all(ch.isalnum() or ch in ".-_" for ch in value)


def _is_client_with_code(part: str) -> bool:
    tokens = part.split()
    return (
        2 <= len(tokens) <= _MAX_TOKENS_PER_CLIENT
        and any(_is_branch_code(token) for token in tokens)
        and not any(_is_instruction_token(token) for token in tokens)
    )


def _split_shared_instruction_clients(raw_text: str) -> list[str] | None:
    """Return the client list of a compact multi-client command, else None.

    Deliberately strict: every part must be a short "name + branch code"
    phrase. Conversational messages ("გაუმარჯოს ომარ, ...") therefore keep
    using the marker-based parser above.
    """
    tokens = raw_text.strip().split()
    start = next(
        (index for index, token in enumerate(tokens) if _is_instruction_token(token)),
        None,
    )
    if not start:  # None, or the message starts with the instruction
        return None
    client_blob = " ".join(tokens[:start])

    parts: list[str] | None = None
    if re.search(r"[,;|\n\r]", client_blob):
        parts = re.split(r"[,;|\n\r]+", client_blob)
    elif re.search(r"\s+და\s+", client_blob):
        parts = re.split(r"\s+და\s+", client_blob)
    else:
        parts = _split_by_branch_codes(client_blob.split())

    if parts is None:
        return None
    parts = [part.strip(_TOKEN_PUNCTUATION) for part in parts]
    parts = [part for part in parts if part]
    if len(parts) < 2 or not all(_is_client_with_code(part) for part in parts):
        return None
    return parts


def _split_by_branch_codes(tokens: list[str]) -> list[str] | None:
    """"გვირილა 0133 გვირილა 0018 რითეილ 238" -> one part per code."""
    parts: list[str] = []
    current: list[str] = []
    for token in tokens:
        current.append(token)
        if _is_branch_code(token):
            parts.append(" ".join(current))
            current = []
    if current:
        return None
    return parts

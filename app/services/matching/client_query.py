from __future__ import annotations

import re

from app.services.matching.normalizer import normalize_text

_NUMERIC = re.compile(r"\d+")
_N_MARKED_NUMBER = re.compile(r"(?i)^n(?P<digits>\d{2,6})$")
_PREFIXED_NUMBER = re.compile(r"(?i)^(?P<prefix>[a-z])(?P<digits>\d{2,6})$")
_DIGIT_LEADING = re.compile(r"^(?P<digits>\d+)[^\d]*$")

# Words that describe the requested visit-plan operation rather than the client.
# Keep this list deliberately narrow and explicit: client names must not be
# accidentally stripped just because they contain a common Georgian word.
_COMMAND_TOKENS = {
    "ჩასვი",
    "ჩამისვი",
    "ჩამიმატე",
    "დაამატე",
    "დამიმატე",
    "დამატებით",
    "დასვი",
    "მონიშნე",
    "ამოიღე",
    "ამოიღეთ",
    "ამოღება",
    "მოხსენი",
    "მოხსენით",
    "მოხსნა",
    "ამოშალე",
    "ამოშალეთ",
    "ამოშლა",
    "წაშალე",
    "წაშალეთ",
    "წაშლა",
    "ჩასმა",
    "დამატება",
    "გადაიტანე",
    "გადმოიტანე",
    "შეცვალე",
    "იყოს",
    "იჯდეს",
    "მხოლოდ",
}

_CONNECTORS = {
    "და",
    "ზე",
    "ზეც",
    "ში",
}

_DAY_TOKENS = {
    "ორშაბათი",
    "ორშაბათს",
    "ორშაბათზე",
    "ორშაბათიდან",
    "ორშაბათზეც",
    "სამშაბათი",
    "სამშაბათს",
    "სამშაბათზე",
    "სამშაბათიდან",
    "სამშაბათზეც",
    "ოთხშაბათი",
    "ოთხშაბათს",
    "ოთხშაბათზე",
    "ოთხშაბათიდან",
    "ოთხშაბათზეც",
    "ხუთშაბათი",
    "ხუთშაბათს",
    "ხუთშაბათზე",
    "ხუთშაბათიდან",
    "ხუთშაბათზეც",
    "პარასკევი",
    "პარასკევს",
    "პარასკევზე",
    "პარასკევიდან",
    "პარასკევზეც",
    "შაბათი",
    "შაბათს",
    "შაბათზე",
    "შაბათიდან",
    "შაბათზეც",
    "კვირა",
    "კვირას",
    "კვირაზე",
    "კვირიდან",
    "კვირაზეც",
    "დღეს",
    "დღევანდელი",
    "დღევანდელზე",
    "დღევანდელს",
    "დღევანდელზეც",
}


def extract_client_identity_text(raw_text: str) -> str:
    """Remove day/operation noise and keep the human client identifier.

    Examples:
      "გვირილა 0203 სამშაბათზე და ხუთშაბათზე ჩასვი"
        -> "გვირილა 0203"
      "#452 ორი ნაბიჯი ჩამისვი დღევანდელზე"
        -> "452 ორი ნაბიჯი"
    """

    normalized = normalize_text(raw_text)
    if not normalized:
        return ""

    kept: list[str] = []
    for token in normalized.split():
        if token in _DAY_TOKENS:
            continue
        if token in _COMMAND_TOKENS:
            continue
        if token in _CONNECTORS:
            continue
        kept.append(token)

    return " ".join(kept).strip()


def canonical_search_text(value: str) -> str:
    """Canonical form for human client identifiers.

    Only N is treated as an optional outlet marker:
      N0158 == 0158 == 158

    Other letter prefixes are meaningful branch identifiers and are preserved:
      S0278 == S278
      K0278 == K278
      D0278 == D278

    A leading # is punctuation and is removed by normalize_text, so #278 == 278.
    This canonicalizer is intentionally used for human-facing identity fields,
    not for the real 1C catalog Code.
    """

    normalized = normalize_text(value)
    if not normalized:
        return ""

    result: list[str] = []
    for token in normalized.split():
        n_marker = _N_MARKED_NUMBER.fullmatch(token)
        if n_marker:
            raw = n_marker.group("digits")
            result.append(raw.lstrip("0") or "0")
            continue

        prefixed = _PREFIXED_NUMBER.fullmatch(token)
        if prefixed:
            prefix = prefixed.group("prefix").casefold()
            raw = prefixed.group("digits")
            result.append(f"{prefix}{raw.lstrip('0') or '0'}")
            continue

        if token.isdigit() and len(token) >= 2:
            result.append(token.lstrip("0") or "0")
            continue
        result.append(token)
    return " ".join(result)


def numeric_tokens(value: str) -> tuple[str, ...]:
    """Return comparable *plain numeric* tokens, ignoring leading zeroes.

    N0203 and 203 intentionally become the same token ("203").
    Meaningful branch prefixes are not collapsed: S278/K278/D278 do not
    contribute the plain numeric token "278".
    """

    result: list[str] = []
    for token in normalize_text(value).split():
        raw: str | None = None
        if token.isdigit():
            raw = token
        else:
            n_marker = _N_MARKED_NUMBER.fullmatch(token)
            if n_marker:
                raw = n_marker.group("digits")
            else:
                digit_leading = _DIGIT_LEADING.fullmatch(token)
                if digit_leading and token[0].isdigit():
                    raw = digit_leading.group("digits")

        if raw is None or len(raw) < 2:
            continue
        canonical = raw.lstrip("0") or "0"
        if canonical not in result:
            result.append(canonical)
    return tuple(result)


def outlet_tokens(value: str) -> tuple[str, ...]:
    """Return comparable outlet/branch identifiers.

    N278, #278 and 278 all become "278". Letter-prefixed branch identifiers
    such as S278, K278 and D278 keep their prefix and therefore remain distinct.
    """

    result: list[str] = []
    for token in canonical_search_text(value).split():
        if token.isdigit() and len(token) >= 2:
            candidate = token
        elif _PREFIXED_NUMBER.fullmatch(token):
            candidate = token
        else:
            continue
        if candidate not in result:
            result.append(candidate)
    return tuple(result)


def text_without_numbers(value: str) -> str:
    tokens: list[str] = []
    for token in normalize_text(value).split():
        if token.isdigit() or _PREFIXED_NUMBER.fullmatch(token):
            continue
        if _DIGIT_LEADING.fullmatch(token) and token[0].isdigit():
            continue
        tokens.append(token)
    return " ".join(tokens).strip()

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


class AgentResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentResolution:
    agent_code: str
    agent_description: str
    source: str
    client_text: str


_AGENT_NAME = re.compile(r"^\s*(?:ps\s*)?(\d{3})\s*$", re.IGNORECASE)
_LEADING_AGENT = re.compile(r"^\s*(?:ps\s*)?(\d{3})(?:\s+|$)", re.IGNORECASE)


class AgentResolver:
    """Resolve a 1C agent from sender metadata and, where allowed, message text.

    Fixed sender mappings are preferred. Mapping keys can be:
      - ``id:+9955...`` for WhatsApp/Viber sender IDs / phone numbers
      - ``name:111`` for profile/display names
      - a plain value, checked against both sender ID and sender name

    A sender can also be configured with more than one allowed agent code. In
    that case the message must start with one of those codes, for example
    ``504 #223 ნიკორა ჩასვი პარასკევზე``.

    Security/safety rules:
      - an unknown sender ID cannot impersonate an agent merely by typing 111;
      - if a fixed sender explicitly types a *different* leading agent code,
        the message is sent to review instead of silently choosing one;
      - message-prefix fallback is kept only for API/manual tests that do not
        provide sender metadata.
    """

    def __init__(
        self,
        *,
        sender_map: dict[str, str] | None = None,
        multi_sender_map: dict[str, list[str] | tuple[str, ...] | set[str]] | None = None,
        agent_prefix: str = "PS",
        allow_message_prefix: bool = True,
    ) -> None:
        self.agent_prefix = agent_prefix
        self.allow_message_prefix = allow_message_prefix
        self._id_map: dict[str, str] = {}
        self._name_map: dict[str, str] = {}
        self._plain_map: dict[str, str] = {}
        self._multi_id_map: dict[str, frozenset[str]] = {}
        self._multi_name_map: dict[str, frozenset[str]] = {}
        self._multi_plain_map: dict[str, frozenset[str]] = {}
        self._known_agent_codes: set[str] = set()

        for raw_key, raw_code in (sender_map or {}).items():
            code = self._normalize_agent_code(raw_code)
            self._known_agent_codes.add(code)
            kind, normalized_key = self._normalize_mapping_key(raw_key)
            target = self._map_for_kind(kind)
            target[normalized_key] = code

        for raw_key, raw_codes in (multi_sender_map or {}).items():
            codes = frozenset(self._normalize_agent_code(value) for value in raw_codes)
            self._known_agent_codes.update(codes)
            if len(codes) < 2:
                raise ValueError(
                    f"multi-agent sender {raw_key!r} must allow at least two agent codes"
                )
            kind, normalized_key = self._normalize_mapping_key(raw_key)
            target = self._multi_map_for_kind(kind)
            target[normalized_key] = codes

    def resolve(
        self,
        *,
        raw_text: str,
        sender_id: str | None = None,
        sender_name: str | None = None,
    ) -> AgentResolution:
        message_match = _LEADING_AGENT.match(raw_text)
        explicit_message_code = message_match.group(1) if message_match else None

        # 1) Sender ID is the strongest identity signal. It may be fixed to one
        # agent, or explicitly configured to choose between a small set.
        if sender_id:
            fixed = self._lookup_fixed_sender_id(sender_id)
            if fixed is not None:
                self._reject_conflicting_explicit_code(
                    fixed_code=fixed,
                    explicit_code=explicit_message_code,
                    sender_label=sender_id,
                )
                return self._resolution(
                    code=fixed,
                    source="sender_id",
                    raw_text=raw_text,
                    message_match=message_match,
                )

            allowed = self._lookup_multi_sender_id(sender_id)
            if allowed is not None:
                return self._resolve_multi_sender(
                    raw_text=raw_text,
                    message_match=message_match,
                    explicit_code=explicit_message_code,
                    allowed=allowed,
                    source="sender_id_message_prefix",
                )

        # 2) Explicit mapping for sender/profile name.
        if sender_name:
            fixed = self._lookup_fixed_sender_name(sender_name)
            if fixed is not None:
                self._reject_conflicting_explicit_code(
                    fixed_code=fixed,
                    explicit_code=explicit_message_code,
                    sender_label=sender_name,
                )
                return self._resolution(
                    code=fixed,
                    source="sender_name_map",
                    raw_text=raw_text,
                    message_match=message_match,
                )

            allowed = self._lookup_multi_sender_name(sender_name)
            if allowed is not None:
                return self._resolve_multi_sender(
                    raw_text=raw_text,
                    message_match=message_match,
                    explicit_code=explicit_message_code,
                    allowed=allowed,
                    source="sender_name_message_prefix",
                )

            # A profile/display name such as 111 / PS111 is useful when the
            # WhatsApp sender name itself carries the agent identifier.
            match = _AGENT_NAME.fullmatch(self._normalize_name(sender_name))
            if match:
                code = match.group(1)
                self._reject_conflicting_explicit_code(
                    fixed_code=code,
                    explicit_code=explicit_message_code,
                    sender_label=sender_name,
                )
                return self._resolution(
                    code=code,
                    source="sender_name",
                    raw_text=raw_text,
                    message_match=message_match,
                )

        # 3) Backward-compatible manual/API mode. Do NOT permit an unknown
        # WhatsApp/Viber sender ID to select an arbitrary agent by typing a code.
        sender_metadata_present = bool(sender_id or sender_name)
        if (
            not sender_metadata_present
            and self.allow_message_prefix
            and explicit_message_code is not None
        ):
            return self._resolution(
                code=explicit_message_code,
                source="message_prefix",
                raw_text=raw_text,
                message_match=message_match,
            )

        if sender_id:
            raise AgentResolutionError(
                f"unknown sender_id {self.normalize_phone(sender_id)!r}; "
                "configure AGENT_SENDER_MAP or AGENT_MULTI_SENDER_MAP"
            )

        raise AgentResolutionError(
            "agent could not be resolved from sender_id/sender_name; "
            "configure AGENT_SENDER_MAP or use an agent-coded sender name"
        )

    def _resolve_multi_sender(
        self,
        *,
        raw_text: str,
        message_match: re.Match[str] | None,
        explicit_code: str | None,
        allowed: frozenset[str],
        source: str,
    ) -> AgentResolution:
        allowed_text = ", ".join(sorted(allowed))
        if not self.allow_message_prefix:
            raise AgentResolutionError(
                f"sender can route to multiple agents ({allowed_text}), but "
                "agent codes in message text are disabled"
            )
        if explicit_code is None:
            raise AgentResolutionError(
                f"agent_required: sender can route to multiple agents; "
                f"start the message with one of: {allowed_text}"
            )
        if explicit_code not in allowed:
            raise AgentResolutionError(
                f"agent_not_allowed: {explicit_code} is not allowed for this sender; "
                f"allowed agents: {allowed_text}"
            )
        return self._resolution(
            code=explicit_code,
            source=source,
            raw_text=raw_text,
            message_match=message_match,
        )

    def _resolution(
        self,
        *,
        code: str,
        source: str,
        raw_text: str,
        message_match: re.Match[str] | None,
    ) -> AgentResolution:
        client_text = raw_text
        if message_match and message_match.group(1) == code:
            client_text = raw_text[message_match.end() :].lstrip()
        return AgentResolution(
            agent_code=code,
            agent_description=f"{self.agent_prefix}{code}",
            source=source,
            client_text=client_text,
        )

    def _reject_conflicting_explicit_code(
        self,
        *,
        fixed_code: str,
        explicit_code: str | None,
        sender_label: str,
    ) -> None:
        if (
            explicit_code is not None
            and explicit_code != fixed_code
            and explicit_code in self._known_agent_codes
        ):
            raise AgentResolutionError(
                f"agent_conflict: sender {sender_label!r} maps to {fixed_code}, "
                f"but message starts with known agent {explicit_code}"
            )

    def _lookup_fixed_sender_id(self, value: str) -> str | None:
        normalized_phone = self.normalize_phone(value)
        return self._id_map.get(normalized_phone) or self._plain_map.get(
            self._normalize_generic(value)
        )

    def _lookup_multi_sender_id(self, value: str) -> frozenset[str] | None:
        normalized_phone = self.normalize_phone(value)
        return self._multi_id_map.get(normalized_phone) or self._multi_plain_map.get(
            self._normalize_generic(value)
        )

    def _lookup_fixed_sender_name(self, value: str) -> str | None:
        return self._name_map.get(self._normalize_name(value)) or self._plain_map.get(
            self._normalize_generic(value)
        )

    def _lookup_multi_sender_name(self, value: str) -> frozenset[str] | None:
        return self._multi_name_map.get(self._normalize_name(value)) or self._multi_plain_map.get(
            self._normalize_generic(value)
        )

    def _normalize_mapping_key(self, raw_key: str) -> tuple[str, str]:
        key = raw_key.strip()
        lower = key.casefold()
        if lower.startswith("id:"):
            return "id", self.normalize_phone(key[3:])
        if lower.startswith("name:"):
            return "name", self._normalize_name(key[5:])
        return "plain", self._normalize_generic(key)

    def _map_for_kind(self, kind: str) -> dict[str, str]:
        if kind == "id":
            return self._id_map
        if kind == "name":
            return self._name_map
        return self._plain_map

    def _multi_map_for_kind(self, kind: str) -> dict[str, frozenset[str]]:
        if kind == "id":
            return self._multi_id_map
        if kind == "name":
            return self._multi_name_map
        return self._multi_plain_map

    @staticmethod
    def _normalize_agent_code(value: str) -> str:
        match = _AGENT_NAME.fullmatch(str(value))
        if match is None:
            raise ValueError(f"invalid agent code mapping value: {value!r}")
        return match.group(1)

    @staticmethod
    def normalize_phone(value: str) -> str:
        """Normalize Georgian phone IDs to canonical 9955XXXXXXXX form.

        WhatsApp normally supplies international numbers, but accepting the
        common +995 / 995 / local 5XXXXXXXX variants makes configuration and
        manual testing less fragile.
        """
        normalized = unicodedata.normalize("NFKC", value)
        digits = "".join(ch for ch in normalized if ch.isdigit())
        if not digits:
            return AgentResolver._normalize_generic(value)
        if digits.startswith("00995"):
            digits = digits[2:]
        if len(digits) == 10 and digits.startswith("0") and digits[1] == "5":
            digits = "995" + digits[1:]
        elif len(digits) == 9 and digits.startswith("5"):
            digits = "995" + digits
        return digits

    @staticmethod
    def _normalize_sender_id(value: str) -> str:
        # Backward-compatible alias retained for callers/tests.
        return AgentResolver.normalize_phone(value)

    @staticmethod
    def _normalize_name(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    @staticmethod
    def _normalize_generic(value: str) -> str:
        normalized = AgentResolver._normalize_name(value)
        digits = "".join(ch for ch in normalized if ch.isdigit())
        if digits and all(ch.isdigit() or not ch.isalnum() for ch in normalized):
            return AgentResolver.normalize_phone(value)
        return normalized

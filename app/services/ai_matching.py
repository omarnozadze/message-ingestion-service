from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
import json
import time
import re
from typing import Any
from uuid import UUID

import httpx
from rapidfuzz import fuzz

from app.core.enums import MatchMethod
from app.services.matching.client_query import canonical_search_text, numeric_tokens
from app.services.matching.normalizer import contains_phrase, normalize_text
from app.services.matching.types import MatchCandidate, MatchResult, RankedMatch
from app.services.multi_visit_request import ClientVisitAction, VisitRequest
from app.services.plan_aliases import address_alias_text, is_address_alias
from app.services.visit_instruction import VisitInstruction, VisitOperation


_AI_CALL_LIMIT = 2
_AI_RETRY_ATTEMPTS = 3
_AI_CALL_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _ai_call_semaphore() -> asyncio.Semaphore:
    """Bound concurrent outbound AI calls per event loop.

    A burst of WhatsApp messages can otherwise open several parse+match HTTP
    connections at once. The live Windows/network path proved less reliable
    under that burst pattern, so AI calls are intentionally bounded.
    """
    loop = asyncio.get_running_loop()
    key = id(loop)
    semaphore = _AI_CALL_SEMAPHORES.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_AI_CALL_LIMIT)
        _AI_CALL_SEMAPHORES[key] = semaphore
    return semaphore


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After")
    if value:
        try:
            return max(0.25, min(float(value), 10.0))
        except ValueError:
            pass
    return min(1.0 * (2**attempt), 4.0)


class AIServiceError(RuntimeError):
    """The AI service could not be reached or returned an invalid response."""


class AIReviewRequired(ValueError):
    """AI understood enough to know that the request should not be auto-applied."""


@dataclass(frozen=True, slots=True)
class ShortlistedCandidate:
    client_id: UUID
    display_name: str
    aliases: tuple[str, ...]
    in_active_plan: bool
    retrieval_score: float


@dataclass(frozen=True, slots=True)
class AIClientResolution:
    action_index: int
    match: MatchResult
    reason: str


class OpenAIAssistedMatching:
    """OpenAI-assisted Georgian request parser and client resolver.

    Safety model:
      * the model never receives 1C credentials and never writes to 1C;
      * parsing is constrained by Structured Outputs;
      * client selection is limited to a deterministic shortlist generated from
        local database records;
      * the selected UUID is validated against that shortlist;
      * callers still perform the normal 1C write + post-verification flow.
    """

    RESPONSES_PATH = "/responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.6-terra",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 45.0,
        min_parse_confidence: float = 0.72,
        min_match_confidence: float = 0.78,
        candidate_limit: int = 12,
        reasoning_effort: str = "low",
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 120.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-5.6-terra"
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 15.0))
        self.min_parse_confidence = min_parse_confidence
        self.min_match_confidence = min_match_confidence
        self.candidate_limit = max(3, min(candidate_limit, 25))
        self.reasoning_effort = reasoning_effort
        self.circuit_breaker_failure_threshold = max(1, circuit_breaker_failure_threshold)
        self.circuit_breaker_cooldown_seconds = max(1.0, circuit_breaker_cooldown_seconds)
        self._circuit_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_lock = asyncio.Lock()
        if not self.api_key:
            raise ValueError("OpenAI API key is required when AI matching is enabled")

    async def parse_visit_request(
        self,
        raw_text: str,
        *,
        agent_code: str,
        business_date: date,
        agent_prefix: str = "PS",
    ) -> VisitRequest:
        weekday = business_date.weekday() + 1
        system = (
            "You parse Georgian WhatsApp instructions from field sales agents into visit-plan "
            "operations. Ignore greetings, politeness, filler, spelling noise, and inflections. "
            "Never invent a client. Preserve the COMPLETE human client identity in client_text, "
            "including a person/company/chain name, street/locality, branch/store number, "
            "and letter suffixes such as 101გ or 3.2. Never reduce a phrase like "
            "'ნესტან წურწუმია ნიკეა' to only 'ნიკეა'. "
            "Weekday numbers are 1=Monday, 2=Tuesday, 3=Wednesday, 4=Thursday, "
            "5=Friday, 6=Saturday, 7=Sunday. The supplied current_weekday is authoritative for "
            "დღეს/დღევანდელზე. Operations: add=keep other days and add target day(s); "
            "remove=remove only target day(s); remove_all=when the wording explicitly says "
            "to remove/delete the client from the plan itself (for example 'გეგმიდან ამომიშალე'), "
            "clear every visit slot and return target_days=[]; move=same client is removed from explicit source "
            "day and added to target day(s); set=make the requested target day(s) the intended "
            "schedule when the wording explicitly means only/replace. If one message removes "
            "clients and inserts replacements, return one action per client. If the same client is "
            "clearly removed from one day and inserted on another, return ONE move action. "
            "The resolved agent_code defines the ONLY agent plan this request may modify. Never "
            "infer or modify another agent's plan from this text. An add affects only the requesting "
            "agent; a remove or move is also scoped only to the requesting agent's plan. If the "
            "intent/day/client phrases cannot be separated safely, set needs_review=true."
        )
        user_payload = {
            "message": raw_text,
            "agent_code": agent_code,
            "business_date": business_date.isoformat(),
            "current_weekday": weekday,
        }
        data = await self._structured_response(
            schema_name="visit_request",
            schema=_VISIT_REQUEST_SCHEMA,
            system=system,
            user=json.dumps(user_payload, ensure_ascii=False),
            max_output_tokens=1600,
        )

        try:
            needs_review = bool(data["needs_review"])
            confidence = float(data["confidence"])
            reason = str(data.get("reason") or "").strip()
            raw_actions = data["actions"]
        except (KeyError, TypeError, ValueError) as exc:
            raise AIServiceError(f"invalid AI visit-request structure: {exc}") from exc

        if needs_review:
            raise AIReviewRequired(reason or "AI could not safely interpret the visit request")
        if confidence < self.min_parse_confidence:
            raise AIReviewRequired(
                reason or f"AI visit-request confidence is too low ({confidence:.2f})"
            )
        if not isinstance(raw_actions, list) or not raw_actions:
            raise AIReviewRequired("AI did not extract any visit-plan action")
        if len(raw_actions) > 12:
            raise AIReviewRequired("too many client actions in one WhatsApp message")

        actions: list[ClientVisitAction] = []
        for item in raw_actions:
            if not isinstance(item, dict):
                raise AIServiceError("invalid AI action item")
            client_text = str(item.get("client_text") or "").strip()
            if not client_text:
                raise AIReviewRequired("AI extracted an empty client identity")
            try:
                operation = VisitOperation(str(item["operation"]))
            except (KeyError, ValueError) as exc:
                raise AIServiceError("invalid AI operation") from exc

            if operation == VisitOperation.REMOVE_ALL:
                target_days = ()
            else:
                target_days = _validated_days(item.get("target_days"))
            source_day = item.get("source_day")
            if source_day is not None:
                try:
                    source_day = int(source_day)
                except (TypeError, ValueError) as exc:
                    raise AIServiceError("invalid AI source weekday") from exc
                if source_day < 1 or source_day > 7:
                    raise AIServiceError("AI source weekday is outside 1..7")

            if operation == VisitOperation.MOVE and source_day is None:
                raise AIReviewRequired(
                    f"move source day was not clear for client: {client_text}"
                )
            if operation != VisitOperation.MOVE:
                source_day = None

            instruction = VisitInstruction(
                agent_code=agent_code,
                agent_description=f"{agent_prefix}{agent_code}",
                target_days=target_days,
                operation=operation,
                source_day=source_day,
            )
            actions.append(ClientVisitAction(client_text=client_text, instruction=instruction))

        return VisitRequest(actions=tuple(actions), is_multi=len(actions) > 1)

    async def resolve_clients(
        self,
        actions: tuple[ClientVisitAction, ...],
        candidates: list[MatchCandidate],
        *,
        preferred_one_c_refs: set[str] | frozenset[str] | None = None,
    ) -> tuple[AIClientResolution, ...]:
        if not actions:
            raise AIReviewRequired("no client actions to resolve")
        preferred = {
            str(ref).strip().lower()
            for ref in (preferred_one_c_refs or set())
            if str(ref).strip()
        }

        shortlists: list[list[ShortlistedCandidate]] = []
        request_actions: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            shortlist = self._shortlist(
                action.client_text,
                candidates,
                preferred_one_c_refs=preferred,
            )
            if not shortlist:
                raise AIReviewRequired(f"client not found: {action.client_text}")
            shortlists.append(shortlist)
            request_actions.append(
                {
                    "action_index": index,
                    "client_query": action.client_text,
                    "operation": action.instruction.operation.value,
                    "source_day": action.instruction.source_day,
                    "target_days": list(action.instruction.target_days),
                    "candidates": [
                        {
                            "candidate_id": str(item.client_id),
                            "name": item.display_name,
                            "aliases": list(item.aliases),
                            "in_active_agent_plan": item.in_active_plan,
                            "retrieval_score": round(item.retrieval_score, 1),
                        }
                        for item in shortlist
                    ],
                }
            )

        system = (
            "You resolve Georgian human client descriptions to a candidate supplied by the local "
            "database. You MUST select only a candidate_id shown for that action; never invent an "
            "ID or client. Internal 1C catalog codes are intentionally not supplied. Match by the "
            "human personal/company/chain name, branch or outlet number, street/locality, building number, and "
            "reasonable Georgian inflection/typo variants. Leading zeroes in store numbers are "
            "equivalent (088 = 88), but never ignore an explicit branch/address number. Letter "
            "suffixes such as 101გ are meaningful. The in_active_agent_plan flag is context for "
            "identity/disambiguation only; it is NOT permission and must never be the sole reason "
            "to reject an otherwise exact client. A client outside the current plan is normal for "
            "add. For remove/move, still identify the exact client even when it is outside the "
            "current plan; the backend enforces whether that operation is allowed. Never infer, "
            "select, remove from, or modify another agent's plan. If two candidates remain truly "
            "plausible or the identity evidence contradicts the query, choose no candidate and set "
            "needs_review=true."
        )
        payload = {"actions": request_actions}
        data = await self._structured_response(
            schema_name="client_resolution",
            schema=_CLIENT_RESOLUTION_SCHEMA,
            system=system,
            user=json.dumps(payload, ensure_ascii=False),
            max_output_tokens=1800,
        )

        raw_resolutions = data.get("resolutions")
        if not isinstance(raw_resolutions, list):
            raise AIServiceError("AI client-resolution response has no resolutions array")

        by_index: dict[int, dict[str, Any]] = {}
        for item in raw_resolutions:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item["action_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if idx in by_index:
                raise AIServiceError(f"duplicate AI resolution for action {idx}")
            by_index[idx] = item

        candidate_by_id = {candidate.client_id: candidate for candidate in candidates}
        resolved: list[AIClientResolution] = []
        for index, action in enumerate(actions):
            item = by_index.get(index)
            if item is None:
                raise AIServiceError(f"missing AI resolution for action {index}")
            needs_review = bool(item.get("needs_review"))
            reason = str(item.get("reason") or "").strip()
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError) as exc:
                raise AIServiceError("invalid AI match confidence") from exc
            selected_text = str(item.get("selected_candidate_id") or "").strip()

            if needs_review or not selected_text:
                raise AIReviewRequired(
                    reason or f"client match requires review: {action.client_text}"
                )
            if confidence < self.min_match_confidence:
                raise AIReviewRequired(
                    reason
                    or f"AI client-match confidence is too low for {action.client_text} ({confidence:.2f})"
                )
            try:
                selected_id = UUID(selected_text)
            except ValueError as exc:
                raise AIServiceError("AI returned an invalid candidate UUID") from exc

            shortlist = shortlists[index]
            allowed = {candidate.client_id: candidate for candidate in shortlist}
            selected = allowed.get(selected_id)
            if selected is None:
                raise AIServiceError("AI selected a client that was not in its candidate shortlist")

            candidate = candidate_by_id.get(selected_id)
            if candidate is None:
                raise AIServiceError("selected AI candidate disappeared from local candidates")

            score = max(0.0, min(100.0, confidence * 100.0))
            evidence = (
                f"ai:{reason or 'selected from local shortlist'};"
                f"retrieval={selected.retrieval_score:.1f};"
                f"plan={selected.in_active_plan};"
                f"name={candidate.display_name}"
            )
            ranked = RankedMatch(
                client_id=selected_id,
                method=MatchMethod.FUZZY,
                score=score,
                evidence=evidence,
            )
            resolved.append(
                AIClientResolution(
                    action_index=index,
                    match=MatchResult(
                        matched=True,
                        ambiguous=False,
                        client_id=selected_id,
                        method=MatchMethod.FUZZY,
                        score=score,
                        evidence=evidence,
                        alternatives=(ranked,),
                    ),
                    reason=reason,
                )
            )

        return tuple(resolved)

    def _shortlist(
        self,
        query_text: str,
        candidates: list[MatchCandidate],
        *,
        preferred_one_c_refs: set[str],
    ) -> list[ShortlistedCandidate]:
        query = canonical_search_text(query_text)
        if not query:
            return []
        query_numbers = set(_identity_numbers(query_text))
        query_tokens = set(normalize_text(query_text).split())

        ranked: list[ShortlistedCandidate] = []
        for candidate in candidates:
            in_plan = bool(
                candidate.one_c_ref
                and candidate.one_c_ref.strip().lower() in preferred_one_c_refs
            )
            raw_terms = [candidate.display_name, candidate.normalized_name, *candidate.aliases]
            scored_terms: list[tuple[float, str]] = []
            best_score = 0.0

            for raw_term in raw_terms:
                if not raw_term:
                    continue
                display_term = (
                    address_alias_text(raw_term) if is_address_alias(raw_term) else raw_term
                )
                term = canonical_search_text(display_term)
                if not term:
                    continue

                base = max(
                    float(fuzz.WRatio(query, term)),
                    float(fuzz.token_set_ratio(query, term)),
                    float(fuzz.partial_ratio(query, term)),
                )
                if query == term:
                    base = 100.0
                elif contains_phrase(term, query) or contains_phrase(query, term):
                    base = max(base, 95.0)

                term_numbers = set(_identity_numbers(display_term))
                number_overlap = query_numbers & term_numbers
                if query_numbers:
                    if number_overlap:
                        base += 18.0
                        # Exact branch-number evidence is extremely useful.
                        if query_numbers <= term_numbers:
                            base += 7.0
                    elif term_numbers:
                        base -= 22.0
                    else:
                        base -= 12.0

                term_tokens = set(normalize_text(display_term).split())
                descriptive_overlap = {
                    token
                    for token in query_tokens & term_tokens
                    if not any(ch.isdigit() for ch in token)
                }
                if descriptive_overlap:
                    base += min(10.0, 2.5 * len(descriptive_overlap))
                if in_plan:
                    base += 4.0

                base = max(0.0, min(100.0, base))
                scored_terms.append((base, normalize_text(display_term)))
                best_score = max(best_score, base)

            if best_score <= 0:
                continue

            aliases: list[str] = []
            for _score, term in sorted(scored_terms, key=lambda x: x[0], reverse=True):
                if not term or term == normalize_text(candidate.display_name):
                    continue
                if term not in aliases:
                    aliases.append(term)
                if len(aliases) >= 8:
                    break

            ranked.append(
                ShortlistedCandidate(
                    client_id=candidate.client_id,
                    display_name=candidate.display_name,
                    aliases=tuple(aliases),
                    in_active_plan=in_plan,
                    retrieval_score=best_score,
                )
            )

        ranked.sort(
            key=lambda item: (item.retrieval_score, item.in_active_plan),
            reverse=True,
        )

        # When explicit numeric identity exists, prefer candidates that preserve
        # those digits somewhere in the human name/aliases. Keep a few looser
        # semantic candidates as insurance against dirty/missing aliases.
        if query_numbers:
            numeric_hits: list[ShortlistedCandidate] = []
            loose: list[ShortlistedCandidate] = []
            candidate_by_id = {candidate.client_id: candidate for candidate in candidates}
            for item in ranked:
                candidate = candidate_by_id[item.client_id]
                human_blob = " ".join(
                    [candidate.display_name, candidate.normalized_name, *candidate.aliases]
                )
                if query_numbers & set(_identity_numbers(human_blob)):
                    numeric_hits.append(item)
                else:
                    loose.append(item)
            combined = numeric_hits[: self.candidate_limit]
            remaining = self.candidate_limit - len(combined)
            if remaining > 0:
                combined.extend(loose[:remaining])
            return combined

        return ranked[: self.candidate_limit]

    async def _structured_response(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        async with self._circuit_lock:
            remaining = self._circuit_open_until - time.monotonic()
            if remaining > 0:
                raise AIServiceError(
                    f"OpenAI circuit breaker is open; retry after {remaining:.1f}s"
                )

        try:
            result = await self._structured_response_request(
                schema_name=schema_name,
                schema=schema,
                system=system,
                user=user,
                max_output_tokens=max_output_tokens,
            )
        except AIServiceError:
            async with self._circuit_lock:
                self._circuit_failures += 1
                if self._circuit_failures >= self.circuit_breaker_failure_threshold:
                    self._circuit_open_until = (
                        time.monotonic() + self.circuit_breaker_cooldown_seconds
                    )
            raise

        async with self._circuit_lock:
            self._circuit_failures = 0
            self._circuit_open_until = 0.0
        return result

    async def _structured_response_request(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response: httpx.Response | None = None
        last_transport_error: BaseException | None = None

        # Parse/match is a support layer, not the source of truth. Retry only
        # transient transport/server failures and keep the retry window short.
        for attempt in range(_AI_RETRY_ATTEMPTS):
            try:
                async with _ai_call_semaphore():
                    async with httpx.AsyncClient(
                        timeout=self.timeout,
                        headers=headers,
                    ) as client:
                        response = await client.post(
                            f"{self.base_url}{self.RESPONSES_PATH}",
                            json=payload,
                        )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_transport_error = exc
                if attempt + 1 < _AI_RETRY_ATTEMPTS:
                    await asyncio.sleep(min(1.0 * (2**attempt), 4.0))
                    continue
                raise AIServiceError(
                    "OpenAI request failed after retries: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if response.status_code in {408, 409, 429, 500, 502, 503, 504}:
                if attempt + 1 < _AI_RETRY_ATTEMPTS:
                    await asyncio.sleep(_retry_after_seconds(response, attempt))
                    continue

            break

        if response is None:
            exc = last_transport_error or RuntimeError("no HTTP response")
            raise AIServiceError(
                f"OpenAI request failed: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = response.text[:1200].strip()
            raise AIServiceError(
                f"OpenAI HTTP {response.status_code}: {detail or 'request failed'}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise AIServiceError("OpenAI returned non-JSON response") from exc

        if body.get("status") == "incomplete":
            raise AIServiceError("OpenAI response was incomplete")

        output_text = _extract_response_text(body)
        if not output_text:
            raise AIServiceError("OpenAI response contained no structured output text")
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise AIServiceError("OpenAI structured output was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise AIServiceError("OpenAI structured output root was not an object")
        return parsed


def _validated_days(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise AIReviewRequired("AI did not identify a target weekday")
    result: list[int] = []
    for raw in value:
        try:
            day = int(raw)
        except (TypeError, ValueError) as exc:
            raise AIServiceError("invalid AI weekday") from exc
        if day < 1 or day > 7:
            raise AIServiceError("AI weekday is outside 1..7")
        if day not in result:
            result.append(day)
    return tuple(result)


def _extract_response_text(body: dict[str, Any]) -> str:
    # Raw Responses API JSON stores text inside output[*].content[*].text.
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    # Keep compatibility with proxies/wrappers that expose output_text directly.
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    return ""


def _identity_numbers(value: str) -> tuple[str, ...]:
    """Branch/number identifiers used only for AI shortlist retrieval.

    N278, #278 and 278 collapse to "278". Meaningful letter prefixes remain
    distinct, so S278, K278 and D278 can never overlap merely because they all
    contain the digits 278. Single plain numeric parts (for example 3.2) are
    still kept as retrieval hints.
    """

    result: list[str] = []
    for token in normalize_text(value).split():
        if token.isdigit():
            canonical = token.lstrip("0") or "0"
        else:
            n_match = re.fullmatch(r"(?i)n(\d+)", token)
            if n_match:
                canonical = n_match.group(1).lstrip("0") or "0"
            else:
                prefixed = re.fullmatch(r"(?i)([a-z])(\d+)", token)
                if prefixed:
                    prefix = prefixed.group(1).casefold()
                    digits = prefixed.group(2).lstrip("0") or "0"
                    canonical = f"{prefix}{digits}"
                else:
                    digit_leading = re.fullmatch(r"(\d+)[^\d]*", token)
                    if not digit_leading or not token[0].isdigit():
                        continue
                    canonical = digit_leading.group(1).lstrip("0") or "0"

        if canonical not in result:
            result.append(canonical)
    return tuple(result)


_VISIT_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "needs_review": {"type": "boolean"},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "client_text": {"type": "string"},
                    "operation": {
                        "type": "string",
                        "enum": ["add", "remove", "remove_all", "move", "set"],
                    },
                    "source_day": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "null"},
                        ]
                    },
                    "target_days": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["client_text", "operation", "source_day", "target_days"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["needs_review", "reason", "confidence", "actions"],
    "additionalProperties": False,
}


_CLIENT_RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resolutions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_index": {"type": "integer"},
                    "selected_candidate_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "needs_review": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "action_index",
                    "selected_candidate_id",
                    "confidence",
                    "needs_review",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["resolutions"],
    "additionalProperties": False,
}

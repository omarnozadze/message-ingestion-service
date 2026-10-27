import asyncio
from datetime import UTC, datetime
import logging
from time import perf_counter
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MatchMethod, MessageSource, MessageStatus
from app.core.exceptions import (
    ActiveClientNotFoundError,
    IncomingMessageNotFoundError,
    InvalidMessageStateError,
)
from app.integrations.one_c.base import OneCClient, OneCVisitChange
from app.integrations.one_c.odata import OneCPlanReviewRequired
from app.models.client import Client
from app.models.incoming_message import IncomingMessage
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.incoming_message_repository import IncomingMessageRepository
from app.schemas.incoming_message import IncomingMessageCreate
from app.services.agent_resolver import AgentResolutionError, AgentResolver
from app.services.ai_matching import (
    AIReviewRequired,
    AIServiceError,
    OpenAIAssistedMatching,
)
from app.services.matching.client_query import canonical_search_text, extract_client_identity_text
from app.services.matching.engine import MatchingEngine
from app.services.matching.types import MatchResult
from app.services.matching.normalizer import normalize_text
from app.services.multi_visit_request import (
    ClientVisitAction,
    MultiVisitRequestParseError,
    parse_visit_request,
)
logger = logging.getLogger(__name__)

_FAST_LOCAL_MATCH_METHODS = {
    MatchMethod.CODE,
    MatchMethod.ALIAS,
    MatchMethod.EXACT_BRANCH,
    MatchMethod.EXACT_NAME,
}


from app.services.visit_instruction import (
    VisitInstruction,
    VisitInstructionParseError,
    parse_visit_instruction,
)


class IncomingMessageService:
    def __init__(
        self,
        *,
        message_repo: IncomingMessageRepository,
        client_repo: ClientRepository,
        audit_repo: AuditLogRepository,
        matcher: MatchingEngine,
        one_c_client: OneCClient,
        agent_resolver: AgentResolver | None = None,
        business_timezone: str = "Asia/Tbilisi",
        ai_matching: OpenAIAssistedMatching | None = None,
    ) -> None:
        self.message_repo = message_repo
        self.client_repo = client_repo
        self.audit_repo = audit_repo
        self.matcher = matcher
        self.one_c_client = one_c_client
        self.agent_resolver = agent_resolver or AgentResolver()
        self.business_timezone = ZoneInfo(business_timezone)
        self.ai_matching = ai_matching

    async def process(
        self, session: AsyncSession, payload: IncomingMessageCreate
    ) -> IncomingMessage:
        if payload.external_message_id:
            existing = await self.message_repo.find_by_external_id(
                session,
                source=payload.source,
                external_message_id=payload.external_message_id,
            )
            if existing is not None:
                return existing

        message = IncomingMessage(
            source=payload.source,
            external_message_id=payload.external_message_id,
            sender_id=payload.sender_id,
            sender_name=payload.sender_name,
            raw_text=payload.text,
            normalized_text=normalize_text(payload.text),
            status=MessageStatus.RECEIVED,
        )
        self.message_repo.add(session, message)
        await session.flush()
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="MESSAGE_RECEIVED",
            payload={
                "source": payload.source.value,
                "sender_id": payload.sender_id,
                "sender_name": payload.sender_name,
            },
        )
        await session.commit()

        try:
            agent = self.agent_resolver.resolve(
                raw_text=payload.text,
                sender_id=payload.sender_id,
                sender_name=payload.sender_name,
            )
        except AgentResolutionError as exc:
            message.status = MessageStatus.NEEDS_REVIEW
            message.error_message = str(exc)
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="AGENT_REVIEW_REQUIRED",
                payload={"error": str(exc)},
            )
            await session.commit()
            await session.refresh(message)
            return message

        message.agent_code = agent.agent_code
        message.agent_description = agent.agent_description
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="AGENT_RESOLVED",
            payload={
                "agent_code": agent.agent_code,
                "agent": agent.agent_description,
                "resolution_source": agent.source,
            },
        )

        business_date = datetime.now(self.business_timezone).date()

        # FAST PATH: deterministic local parsing is effectively free and handles
        # the normal WhatsApp command grammar (for example
        # "115 რითეილ S225 პარასკევზე ჩასვი"). AI is now a fallback for
        # requests the local parser cannot safely understand, rather than a
        # mandatory network round-trip before every 1C write.
        try:
            visit_request = parse_visit_request(
                agent.client_text,
                agent_code=agent.agent_code,
                business_date=business_date,
            )
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="LOCAL_VISIT_REQUEST_PARSED_FAST_PATH",
                payload={"action_count": len(visit_request.actions)},
            )
        except (VisitInstructionParseError, MultiVisitRequestParseError) as local_exc:
            if self.ai_matching is None:
                message.status = MessageStatus.NEEDS_REVIEW
                message.error_message = str(local_exc)
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="VISIT_INSTRUCTION_REVIEW_REQUIRED",
                    payload={"error": str(local_exc)},
                )
                await session.commit()
                await session.refresh(message)
                return message

            try:
                visit_request = await asyncio.wait_for(
                    self.ai_matching.parse_visit_request(
                        agent.client_text,
                        agent_code=agent.agent_code,
                        business_date=business_date,
                    ),
                    timeout=25.0,
                )
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_VISIT_REQUEST_PARSED",
                    payload={
                        "local_parser_error": str(local_exc),
                        "actions": [
                            {
                                "client_text": action.client_text,
                                "operation": action.instruction.operation.value,
                                "source_day": action.instruction.source_day,
                                "target_days": list(action.instruction.target_days),
                            }
                            for action in visit_request.actions
                        ],
                    },
                )
            except AIReviewRequired as exc:
                message.status = MessageStatus.NEEDS_REVIEW
                message.error_message = f"AI review required: {exc}"
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_VISIT_REQUEST_REVIEW_REQUIRED",
                    payload={"error": str(exc)},
                )
                await session.commit()
                await session.refresh(message)
                return message
            except (AIServiceError, TimeoutError) as exc:
                message.status = MessageStatus.NEEDS_REVIEW
                message.error_message = (
                    "AI temporarily unavailable and local parser could not safely "
                    f"understand the request: {local_exc}"
                )
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_SERVICE_ERROR",
                    payload={
                        "stage": "parse",
                        "error": str(exc),
                        "local_parser_error": str(local_exc),
                    },
                )
                await session.commit()
                await session.refresh(message)
                return message

        preferred_one_c_refs: set[str] = set()
        try:
            preferred_one_c_refs = await self.one_c_client.get_active_plan_partner_refs(
                agent_description=agent.agent_description,
                business_date=business_date,
            )
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="MATCH_CONTEXT_LOADED",
                payload={
                    "agent": agent.agent_description,
                    "active_plan_partner_count": len(preferred_one_c_refs),
                },
            )
        except Exception as exc:
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="MATCH_CONTEXT_UNAVAILABLE",
                payload={
                    "agent": agent.agent_description,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

        candidates = await self.client_repo.list_active_match_candidates(session)

        # Deterministic local matching, run once per action:
        #   1. the requesting agent's active plan first (existing plan clients
        #      resolve immediately and win over look-alikes elsewhere);
        #   2. otherwise the full cached Partners index (new clients).
        # The full-index result is also persisted as MATCH_DIAGNOSTICS so every
        # decision is explainable without replaying it.
        plan_candidates = [
            candidate
            for candidate in candidates
            if candidate.one_c_ref
            and candidate.one_c_ref.lower() in preferred_one_c_refs
        ]
        local_matches: dict[int, MatchResult] = {}
        for action_index, action in enumerate(visit_request.actions):
            full_match = self.matcher.match(
                action.client_text,
                candidates,
                preferred_one_c_refs=preferred_one_c_refs,
            )
            self._audit_match_diagnostics(
                session,
                message=message,
                action_index=action_index,
                query=action.client_text,
                candidates=candidates,
                preferred_one_c_refs=preferred_one_c_refs,
                local_result=full_match,
            )
            plan_match = self.matcher.match(
                action.client_text,
                plan_candidates,
                preferred_one_c_refs=preferred_one_c_refs,
            )
            if plan_match.matched and plan_match.method in _FAST_LOCAL_MATCH_METHODS:
                local_matches[action_index] = plan_match
            else:
                local_matches[action_index] = full_match
        all_local_strong = all(
            match.matched and match.method in _FAST_LOCAL_MATCH_METHODS
            for match in local_matches.values()
        )

        ai_matches: dict[int, object] = {}
        ai_resolution_available = False
        if self.ai_matching is not None and not all_local_strong:
            ai_resolution_available = True
            try:
                ai_resolutions = await asyncio.wait_for(
                    self.ai_matching.resolve_clients(
                        visit_request.actions,
                        candidates,
                        preferred_one_c_refs=preferred_one_c_refs,
                    ),
                    timeout=25.0,
                )
                ai_matches = {item.action_index: item.match for item in ai_resolutions}
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_CLIENTS_RESOLVED",
                    payload={
                        "items": [
                            {
                                "action_index": item.action_index,
                                "client_id": str(item.match.client_id),
                                "score": item.match.score,
                                "evidence": item.match.evidence,
                                "reason": item.reason,
                            }
                            for item in ai_resolutions
                        ]
                    },
                )
            except AIReviewRequired as exc:
                message.status = MessageStatus.NEEDS_REVIEW
                message.error_message = f"AI review required: {exc}"
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_MATCH_REVIEW_REQUIRED",
                    payload={"error": str(exc)},
                )
                await session.commit()
                await session.refresh(message)
                return message
            except (AIServiceError, TimeoutError) as exc:
                # Keep processing with the local scorer. The shortlist/database
                # remains authoritative, so an AI network outage must not stop
                # straightforward exact/alias/address matches.
                ai_resolution_available = False
                ai_matches = {}
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_SERVICE_ERROR",
                    payload={"stage": "match", "error": str(exc), "fallback": "local"},
                )
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AI_MATCH_FALLBACK_USED",
                    payload={"reason": str(exc)},
                )
        elif self.ai_matching is not None and all_local_strong:
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="AI_MATCH_SKIPPED_FAST_PATH",
                payload={
                    "reason": "all actions resolved by deterministic local matcher",
                    "methods": [
                        local_matches[index].method.value
                        for index in sorted(local_matches)
                        if local_matches[index].method is not None
                    ],
                },
            )

        if not visit_request.is_multi:
            action = visit_request.actions[0]
            instruction = action.instruction
            match = ai_matches.get(0) if ai_resolution_available else local_matches[0]

            if not match.matched:
                candidate_by_id = {candidate.client_id: candidate for candidate in candidates}
                message.status = MessageStatus.NEEDS_REVIEW
                message.error_message = (
                    "multiple possible clients found"
                    if match.ambiguous
                    else "client not found"
                )
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="MATCH_REVIEW_REQUIRED",
                    payload={
                        "query": action.client_text,
                        "normalized_query": canonical_search_text(action.client_text),
                        "identity": extract_client_identity_text(action.client_text) or action.client_text,
                        "ambiguous": match.ambiguous,
                        "active_plan_partner_count": len(preferred_one_c_refs),
                        "alternatives": [
                            {
                                "client_id": str(item.client_id),
                                "client_name": (candidate_by_id[item.client_id].display_name if item.client_id in candidate_by_id else None),
                                "method": item.method.value,
                                "score": item.score,
                                "evidence": item.evidence,
                            }
                            for item in match.alternatives
                        ],
                    },
                )
                await session.commit()
                await session.refresh(message)
                return message

            client = await self.client_repo.get_active_by_id(
                session, client_id=match.client_id
            )
            if client is None:
                raise ActiveClientNotFoundError(match.client_id)

            if (
                instruction.operation.value in {"remove", "remove_all", "move"}
                and preferred_one_c_refs
                and client.one_c_ref
                and client.one_c_ref.lower() not in preferred_one_c_refs
            ):
                operation = instruction.operation.value
                message.status = MessageStatus.NEEDS_REVIEW
                message.error_message = (
                    f"client is not in requesting agent plan for {operation}: "
                    f"{action.client_text}"
                )
                self.audit_repo.add(
                    session,
                    incoming_message_id=message.id,
                    event_type="AGENT_PLAN_SCOPE_BLOCKED",
                    payload={
                        "reason": "client is not in requesting agent plan",
                        "operation": operation,
                        "agent": agent.agent_description,
                        "client_text": action.client_text,
                        "client_id": str(client.id),
                        "policy": "never modify another agent plan automatically",
                    },
                )
                await session.commit()
                await session.refresh(message)
                return message

            message.matched_client_id = match.client_id
            message.match_method = match.method
            message.match_score = match.score
            message.match_evidence = match.evidence

            message.status = MessageStatus.SYNC_PENDING
            message.error_message = None
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="CLIENT_MATCHED",
                payload={
                    "query": action.client_text,
                    "normalized_query": canonical_search_text(action.client_text),
                    "client_id": str(match.client_id),
                    "client_name": client.display_name,
                    "method": match.method.value if match.method else None,
                    "score": match.score,
                    "evidence": match.evidence,
                    "resolution_source": "ai" if ai_resolution_available else "local",
                },
            )
            self._audit_instruction(session, message, instruction)
            await session.commit()

            return await self._sync_to_one_c(
                session,
                message=message,
                client=client,
                instruction=instruction,
            )

        # Multi-client request: resolve EVERY client before touching 1C.
        # If one phrase is ambiguous or missing, the whole request stops here.
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="MULTI_VISIT_REQUEST_PARSED",
            payload={
                "actions": [
                    {
                        "client_text": action.client_text,
                        "operation": action.instruction.operation.value,
                        "target_days": list(action.instruction.target_days),
                    }
                    for action in visit_request.actions
                ]
            },
        )

        resolved: list[tuple[ClientVisitAction, Client, object]] = []
        review_items: list[dict] = []

        for action_index, action in enumerate(visit_request.actions):
            match = (
                ai_matches.get(action_index)
                if ai_resolution_available
                else local_matches[action_index]
            )
            if not match.matched:
                review_items.append(
                    {
                        "client_text": action.client_text,
                        "reason": (
                            "multiple possible clients found"
                            if match.ambiguous
                            else "client not found"
                        ),
                        "ambiguous": match.ambiguous,
                        "alternatives": [
                            {
                                "client_id": str(item.client_id),
                                "method": item.method.value,
                                "score": item.score,
                                "evidence": item.evidence,
                            }
                            for item in match.alternatives
                        ],
                    }
                )
                continue

            client = await self.client_repo.get_active_by_id(
                session, client_id=match.client_id
            )
            if client is None:
                review_items.append(
                    {
                        "client_text": action.client_text,
                        "reason": "matched client is not active",
                        "ambiguous": False,
                        "alternatives": [],
                    }
                )
                continue

            if not client.one_c_ref:
                review_items.append(
                    {
                        "client_text": action.client_text,
                        "reason": "matched client has no 1C Partner Ref_Key",
                        "ambiguous": False,
                        "alternatives": [],
                    }
                )
                continue

            # Scope rule: a WhatsApp request may change only the requesting
            # agent's plan. ADD may create a new row in that plan. REMOVE/REMOVE_ALL/MOVE
            # must already exist there; never infer or modify another agent's plan.
            if (
                action.instruction.operation.value in {"remove", "remove_all", "move"}
                and preferred_one_c_refs
                and client.one_c_ref.lower() not in preferred_one_c_refs
            ):
                operation = action.instruction.operation.value
                review_items.append(
                    {
                        "client_text": action.client_text,
                        "reason": f"client is not in requesting agent plan for {operation}",
                        "ambiguous": False,
                        "alternatives": [],
                    }
                )
                continue

            resolved.append((action, client, match))

        if review_items:
            message.status = MessageStatus.NEEDS_REVIEW
            first = review_items[0]
            message.error_message = f"{first['reason']}: {first['client_text']}"
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="MULTI_MATCH_REVIEW_REQUIRED",
                payload={
                    "items": review_items,
                    "resolved_count": len(resolved),
                    "requested_count": len(visit_request.actions),
                },
            )
            await session.commit()
            await session.refresh(message)
            return message

        first_action, first_client, first_match = resolved[0]
        message.matched_client_id = first_client.id
        message.match_method = first_match.method
        message.match_score = first_match.score
        operation_counts: dict[str, int] = {}
        for action, _client, _match in resolved:
            key = action.instruction.operation.value
            operation_counts[key] = operation_counts.get(key, 0) + 1
        summary_parts = [f"multi:{len(resolved)} clients"]
        summary_parts.extend(
            f"{operation}={count}"
            for operation, count in sorted(operation_counts.items())
        )
        summary_parts.append(f"first={first_match.evidence or ''}")
        message.match_evidence = "; ".join(summary_parts)[:512]
        message.status = MessageStatus.SYNC_PENDING
        message.error_message = None

        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="MULTI_CLIENTS_MATCHED",
            payload={
                "clients": [
                    {
                        "client_text": action.client_text,
                        "client_id": str(client.id),
                        "client_name": client.display_name,
                        "one_c_ref": client.one_c_ref,
                        "operation": action.instruction.operation.value,
                        "target_days": list(action.instruction.target_days),
                        "match_method": match.method.value if match.method else None,
                        "match_score": match.score,
                        "match_evidence": match.evidence,
                    }
                    for action, client, match in resolved
                ]
            },
        )
        await session.commit()

        return await self._sync_multi_to_one_c(
            session,
            message=message,
            resolved=resolved,
        )

    def _audit_match_diagnostics(
        self,
        session: AsyncSession,
        *,
        message: IncomingMessage,
        action_index: int,
        query: str,
        candidates: list,
        preferred_one_c_refs: set[str],
        local_result: MatchResult,
    ) -> None:
        identity = extract_client_identity_text(query) or query
        ranked = self.matcher.rank_for_review(
            query,
            candidates,
            preferred_one_c_refs=preferred_one_c_refs,
            limit=5,
        )
        candidate_by_id = {candidate.client_id: candidate for candidate in candidates}
        top_candidates = [
            {
                "client_id": str(item.client_id),
                "client_name": (
                    candidate_by_id[item.client_id].display_name
                    if item.client_id in candidate_by_id
                    else None
                ),
                "score": round(float(item.score), 2),
                "method": item.method.value,
                "evidence": item.evidence,
                "in_active_plan": bool(
                    item.client_id in candidate_by_id
                    and candidate_by_id[item.client_id].one_c_ref
                    and candidate_by_id[item.client_id].one_c_ref.lower()
                    in preferred_one_c_refs
                ),
            }
            for item in ranked
        ]
        payload = {
            "action_index": action_index,
            "query": query,
            "normalized_query": canonical_search_text(query),
            "identity": identity,
            "candidate_count": len(candidates),
            "local_result": {
                "matched": local_result.matched,
                "ambiguous": local_result.ambiguous,
                "client_id": str(local_result.client_id) if local_result.client_id else None,
                "method": local_result.method.value if local_result.method else None,
                "score": local_result.score,
                "evidence": local_result.evidence,
            },
            "top_candidates": top_candidates,
        }
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="MATCH_DIAGNOSTICS",
            payload=payload,
        )
        logger.info(
            "match diagnostics message=%s action=%s query=%r normalized=%r top=%s local=%s",
            message.id,
            action_index,
            query,
            payload["normalized_query"],
            [
                (item["client_name"], item["score"], item["method"])
                for item in top_candidates[:3]
            ],
            payload["local_result"],
        )

    async def get_by_external_id(
        self,
        session: AsyncSession,
        *,
        source: MessageSource,
        external_message_id: str,
    ) -> IncomingMessage | None:
        return await self.message_repo.find_by_external_id(
            session,
            source=source,
            external_message_id=external_message_id,
        )

    async def list_messages(
        self,
        session: AsyncSession,
        *,
        status: MessageStatus | None,
        limit: int,
        offset: int,
    ) -> list[IncomingMessage]:
        return await self.message_repo.list_by_status(
            session,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def resolve_manually(
        self,
        session: AsyncSession,
        *,
        message_id: UUID,
        client_id: UUID,
    ) -> IncomingMessage:
        message = await self.message_repo.get_by_id_for_update(
            session,
            message_id=message_id,
        )
        if message is None:
            raise IncomingMessageNotFoundError(message_id)

        if (
            message.status == MessageStatus.SYNCED
            and message.match_method == MatchMethod.MANUAL
            and message.matched_client_id == client_id
        ):
            return message

        if message.status != MessageStatus.NEEDS_REVIEW:
            raise InvalidMessageStateError(
                current_status=message.status.value,
                expected_status=MessageStatus.NEEDS_REVIEW.value,
            )

        client = await self.client_repo.get_active_by_id(
            session,
            client_id=client_id,
        )
        if client is None:
            raise ActiveClientNotFoundError(client_id)

        business_date = datetime.now(self.business_timezone).date()
        try:
            if message.agent_code:
                agent_code = message.agent_code
                client_text = message.raw_text
            else:
                agent = self.agent_resolver.resolve(
                    raw_text=message.raw_text,
                    sender_id=message.sender_id,
                    sender_name=message.sender_name,
                )
                agent_code = agent.agent_code
                client_text = agent.client_text
                message.agent_code = agent.agent_code
                message.agent_description = agent.agent_description

            instruction = parse_visit_instruction(
                client_text,
                agent_code=agent_code,
                business_date=business_date,
            )
        except (AgentResolutionError, VisitInstructionParseError) as exc:
            message.error_message = str(exc)
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="VISIT_INSTRUCTION_REVIEW_REQUIRED",
                payload={"error": str(exc)},
            )
            await session.commit()
            await session.refresh(message)
            return message

        message.matched_client_id = client.id
        message.match_method = MatchMethod.MANUAL
        message.match_score = 100.0
        message.match_evidence = f"manual:{client.id}"
        message.status = MessageStatus.SYNC_PENDING
        message.error_message = None

        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="MANUALLY_RESOLVED",
            payload={
                "client_id": str(client.id),
                "client_code": client.code,
                "client_name": client.display_name,
            },
        )
        self._audit_instruction(session, message, instruction)
        await session.commit()

        return await self._sync_to_one_c(
            session,
            message=message,
            client=client,
            instruction=instruction,
        )

    def _audit_instruction(
        self,
        session: AsyncSession,
        message: IncomingMessage,
        instruction: VisitInstruction,
    ) -> None:
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="VISIT_INSTRUCTION_PARSED",
            payload={
                "agent_code": instruction.agent_code,
                "agent": instruction.agent_description,
                "operation": instruction.operation.value,
                "source_day": instruction.source_day,
                "target_days": list(instruction.target_days),
                "target_fields": list(instruction.target_fields),
            },
        )

    async def _sync_multi_to_one_c(
        self,
        session: AsyncSession,
        *,
        message: IncomingMessage,
        resolved: list[tuple[ClientVisitAction, Client, object]],
    ) -> IncomingMessage:
        business_date = datetime.now(self.business_timezone).date()
        changes = tuple(
            OneCVisitChange(
                partner_ref=client.one_c_ref or "",
                instruction=action.instruction,
                client_label=client.display_name,
            )
            for action, client, _match in resolved
        )

        try:
            sync_result = await self.one_c_client.apply_visit_instructions(
                changes=changes,
                business_date=business_date,
                source_message_id=message.id,
                raw_text=message.raw_text,
            )
        except OneCPlanReviewRequired as exc:
            message.status = MessageStatus.NEEDS_REVIEW
            message.error_message = str(exc)
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="ONE_C_PLAN_REVIEW_REQUIRED",
                payload={"error": str(exc), "multi": True},
            )
            await session.commit()
            await session.refresh(message)
            return message
        except Exception as exc:
            error_text = str(exc).strip() or type(exc).__name__
            error_text = f"{type(exc).__name__}: {error_text}"
            message.status = MessageStatus.FAILED
            message.error_message = error_text
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="ONE_C_SYNC_FAILED",
                payload={"error": error_text, "multi": True},
            )
            await session.commit()
            await session.refresh(message)
            return message

        message.status = MessageStatus.SYNCED
        message.one_c_record_ref = sync_result.record_ref
        message.one_c_synced_at = datetime.now(UTC)
        message.error_message = None
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="ONE_C_SYNCED",
            payload={
                "record_ref": sync_result.record_ref,
                "details": sync_result.details or {},
                "multi": True,
                "change_count": len(changes),
            },
        )
        await session.commit()
        await session.refresh(message)
        return message

    async def _sync_to_one_c(
        self,
        session: AsyncSession,
        *,
        message: IncomingMessage,
        client: Client,
        instruction: VisitInstruction,
    ) -> IncomingMessage:
        if not client.one_c_ref:
            message.status = MessageStatus.NEEDS_REVIEW
            message.error_message = "matched client has no 1C Partner Ref_Key"
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="ONE_C_PARTNER_REF_MISSING",
                payload={"client_id": str(client.id)},
            )
            await session.commit()
            await session.refresh(message)
            return message

        business_date = datetime.now(self.business_timezone).date()
        one_c_started = perf_counter()
        try:
            sync_result = await self.one_c_client.apply_visit_instruction(
                partner_ref=client.one_c_ref,
                business_date=business_date,
                source_message_id=message.id,
                raw_text=message.raw_text,
                instruction=instruction,
            )
        except OneCPlanReviewRequired as exc:
            message.status = MessageStatus.NEEDS_REVIEW
            message.error_message = str(exc)
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="ONE_C_PLAN_REVIEW_REQUIRED",
                payload={"error": str(exc)},
            )
            await session.commit()
            await session.refresh(message)
            return message
        except Exception as exc:
            error_text = str(exc).strip() or type(exc).__name__
            error_text = f"{type(exc).__name__}: {error_text}"
            message.status = MessageStatus.FAILED
            message.error_message = error_text
            self.audit_repo.add(
                session,
                incoming_message_id=message.id,
                event_type="ONE_C_SYNC_FAILED",
                payload={"error": error_text},
            )
            await session.commit()
            await session.refresh(message)
            return message

        message.status = MessageStatus.SYNCED
        message.one_c_record_ref = sync_result.record_ref
        message.one_c_synced_at = datetime.now(UTC)
        message.error_message = None
        self.audit_repo.add(
            session,
            incoming_message_id=message.id,
            event_type="ONE_C_SYNCED",
            payload={
                "record_ref": sync_result.record_ref,
                "details": sync_result.details or {},
                "elapsed_seconds": round(perf_counter() - one_c_started, 3),
            },
        )
        await session.commit()
        await session.refresh(message)
        return message

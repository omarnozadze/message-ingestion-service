from functools import lru_cache

from app.core.config import settings
from app.integrations.one_c.mock import MockOneCClient
from app.integrations.one_c.odata import OneCODataClient
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.incoming_message_repository import IncomingMessageRepository
from app.services.agent_resolver import AgentResolver
from app.services.ai_matching import OpenAIAssistedMatching
from app.services.incoming_message_service import IncomingMessageService
from app.services.matching.engine import MatchingEngine


@lru_cache
def get_incoming_message_service() -> IncomingMessageService:
    if settings.one_c_mode == "mock":
        one_c_client = MockOneCClient()
    elif settings.one_c_mode == "odata":
        if not settings.one_c_username or not settings.one_c_password:
            raise RuntimeError("ONE_C_USERNAME and ONE_C_PASSWORD are required in odata mode")
        one_c_client = OneCODataClient(
            base_url=settings.one_c_base_url,
            username=settings.one_c_username,
            password=settings.one_c_password,
            timeout_seconds=settings.one_c_timeout_seconds,
        )
    else:
        raise RuntimeError(f"Unsupported ONE_C_MODE={settings.one_c_mode!r}")

    ai_matching = None
    if settings.ai_matching_enabled:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required when AI_MATCHING_ENABLED=true"
            )
        ai_matching = OpenAIAssistedMatching(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.openai_timeout_seconds,
            min_parse_confidence=settings.ai_min_parse_confidence,
            min_match_confidence=settings.ai_min_match_confidence,
            candidate_limit=settings.ai_candidate_limit,
            reasoning_effort=settings.ai_reasoning_effort,
            circuit_breaker_failure_threshold=settings.ai_circuit_breaker_failure_threshold,
            circuit_breaker_cooldown_seconds=settings.ai_circuit_breaker_cooldown_seconds,
        )

    return IncomingMessageService(
        message_repo=IncomingMessageRepository(),
        client_repo=ClientRepository(),
        audit_repo=AuditLogRepository(),
        matcher=MatchingEngine(
            fuzzy_threshold=settings.fuzzy_match_threshold,
            ambiguity_margin=settings.fuzzy_ambiguity_margin,
        ),
        one_c_client=one_c_client,
        agent_resolver=AgentResolver(
            sender_map=settings.agent_sender_map,
            multi_sender_map=settings.agent_multi_sender_map,
            agent_prefix=settings.agent_description_prefix,
            allow_message_prefix=settings.allow_agent_code_in_message,
        ),
        business_timezone=settings.business_timezone,
        ai_matching=ai_matching,
    )

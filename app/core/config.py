from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "message-ingestion-service"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/message_ingestion"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_disable_prepared_statements: bool = False

    fuzzy_match_threshold: float = 88.0
    fuzzy_ambiguity_margin: float = 5.0

    # Client chains excluded from the local search/matching index.
    # Override with JSON in EXCLUDED_CLIENT_NAME_TERMS, for example:
    # ["ორი ნაბიჯი","ფუდმარტი","ნიკორა","ზღაპარი","დეილი"]
    excluded_client_name_terms: tuple[str, ...] = (
        "ორი ნაბიჯი",
        "ფუდმარტი",
        "ნიკორა",
        "ზღაპარი",
        "დეილი",
    )

    # Process-local candidate cache. Successful 1C syncs invalidate it immediately.
    client_candidate_cache_ttl_seconds: int = 300

    one_c_mode: str = "mock"
    one_c_base_url: str = "http://1c-server/WestInvestBotAPI/odata/standard.odata"
    one_c_username: str = ""
    one_c_password: str = ""
    one_c_timeout_seconds: float = 30.0
    business_timezone: str = "Asia/Tbilisi"

    # Automatic 1C search-index synchronization.
    one_c_auto_sync_enabled: bool = True
    one_c_sync_interval_minutes: int = 30
    one_c_sync_overlap_minutes: int = 5
    one_c_full_sync_hour: int = 3
    one_c_sync_startup_delay_seconds: int = 5

    # Optional OpenAI-assisted Georgian parsing + client resolution.
    # Keep disabled until OPENAI_API_KEY is configured locally.
    ai_matching_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-terra"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 45.0
    ai_min_parse_confidence: float = 0.72
    ai_min_match_confidence: float = 0.78
    ai_candidate_limit: int = 12
    ai_reasoning_effort: str = "low"
    ai_circuit_breaker_failure_threshold: int = 5
    ai_circuit_breaker_cooldown_seconds: float = 120.0

    # JSON object. Examples:
    # {"id:+995599123456":"111","name:Sales 112":"112"}
    agent_sender_map: dict[str, str] = Field(default_factory=dict)
    # Sender IDs/names that may route to more than one agent. The message
    # must begin with one of the allowed three-digit agent codes.
    agent_multi_sender_map: dict[str, list[str]] = Field(default_factory=dict)
    agent_description_prefix: str = "PS"
    allow_agent_code_in_message: bool = True

    # Meta WhatsApp Cloud API webhook settings.
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

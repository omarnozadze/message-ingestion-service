from enum import StrEnum


class MessageSource(StrEnum):
    API = "api"
    WHATSAPP = "whatsapp"
    VIBER = "viber"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    SYNC_PENDING = "sync_pending"
    SYNCED = "synced"
    FAILED = "failed"


class MatchMethod(StrEnum):
    CODE = "code"
    ALIAS = "alias"
    EXACT_BRANCH = "exact_branch"
    EXACT_NAME = "exact_name"
    FUZZY = "fuzzy"
    MANUAL = "manual"

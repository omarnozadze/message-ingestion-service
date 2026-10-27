from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MatchMethod, MessageSource, MessageStatus


class IncomingMessageCreate(BaseModel):
    source: MessageSource = MessageSource.API
    external_message_id: str | None = Field(default=None, max_length=256)
    sender_id: str | None = Field(default=None, max_length=256)
    sender_name: str | None = Field(default=None, max_length=256)
    text: str = Field(min_length=1, max_length=10_000)


class ManualResolveRequest(BaseModel):
    client_id: UUID


class IncomingMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: MessageSource
    external_message_id: str | None
    sender_id: str | None
    sender_name: str | None
    agent_code: str | None
    agent_description: str | None
    raw_text: str
    status: MessageStatus
    matched_client_id: UUID | None
    match_method: MatchMethod | None
    match_score: float | None
    match_evidence: str | None
    one_c_record_ref: str | None
    one_c_synced_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

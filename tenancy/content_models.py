# tenancy/content_models.py
from __future__ import annotations

import uuid
import datetime as dt
from sqlalchemy import Column, String, DateTime, Text, Integer, Index, ForeignKey
from auth.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class SpecDocument(Base):
    __tablename__ = "spec_documents"
    __table_args__ = (
        Index("ix_spec_documents_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_spec_documents_tenant_id__id", "tenant_id", "id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(64), nullable=True, index=True)

    source = Column(String(32), nullable=False, default="paste")  # paste|upload|ticket
    filename = Column(String(255), nullable=True)
    mime_type = Column(String(120), nullable=True)

    raw_text = Column(Text, nullable=False)
    meta_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)


class SpecChunk(Base):
    __tablename__ = "spec_chunks"
    __table_args__ = (
        Index("ix_spec_chunks_spec_id__chunk_index", "spec_id", "chunk_index"),
        Index("ix_spec_chunks_tenant_id__spec_id", "tenant_id", "spec_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(64), nullable=False, index=True)

    spec_id = Column(String(36), ForeignKey("spec_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)

    content = Column(Text, nullable=False)
    start_char = Column(Integer, nullable=True)
    end_char = Column(Integer, nullable=True)
    meta_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_tenant_id__updated_at", "tenant_id", "updated_at"),
        Index("ix_conversations_tenant_id__id", "tenant_id", "id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(64), nullable=True, index=True)

    title = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    meta_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_conversation_id__created_at", "conversation_id", "created_at"),
        Index("ix_chat_messages_tenant_id__conversation_id", "tenant_id", "conversation_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    tenant_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(64), nullable=True, index=True)

    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False)  # user|assistant|tool
    content = Column(Text, nullable=False)

    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

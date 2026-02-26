# agent/chat_orchestrator.py
from __future__ import annotations

import datetime as dt
from sqlalchemy.orm import Session
from sqlalchemy import select

from tenancy.content_models import Conversation, ChatMessage


DEFAULT_BUFFER_TURNS = 16  # last N messages in prompt
SUMMARY_TRIGGER_TURNS = 40  # after this, update rolling summary


def _now():
    return dt.datetime.utcnow()


def get_or_create_conversation(db: Session, tenant_id: str, account_id: str | None, conversation_id: str | None = None) -> Conversation:
    if conversation_id:
        conv = db.execute(
            select(Conversation).where(Conversation.tenant_id == str(tenant_id), Conversation.id == conversation_id)
        ).scalar_one_or_none()
        if conv:
            return conv

    conv = Conversation(
        tenant_id=str(tenant_id),
        account_id=str(account_id) if account_id is not None else None,
        title="QA Orchestration Chat",
        summary=None,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def add_message(db: Session, tenant_id: str, account_id: str | None, conversation_id: str, role: str, content: str):
    msg = ChatMessage(
        tenant_id=str(tenant_id),
        account_id=str(account_id) if account_id is not None else None,
        conversation_id=conversation_id,
        role=role,
        content=content,
        created_at=_now(),
    )
    db.add(msg)
    # update conversation updated_at
    conv = db.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = _now()
    db.commit()


def get_recent_messages(db: Session, tenant_id: str, conversation_id: str, limit: int = DEFAULT_BUFFER_TURNS) -> list[ChatMessage]:
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.tenant_id == str(tenant_id), ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return list(reversed(rows))


def count_messages(db: Session, tenant_id: str, conversation_id: str) -> int:
    # quick count by loading ids (fine for sqlite scale; optimize later)
    rows = db.execute(
        select(ChatMessage.id).where(ChatMessage.tenant_id == str(tenant_id), ChatMessage.conversation_id == conversation_id)
    ).all()
    return len(rows)


def maybe_update_summary(db: Session, tenant_id: str, conversation_id: str):
    """
    Rolling summary using the same LLM you already use for explain_mode.
    """
    total = count_messages(db, tenant_id, conversation_id)
    if total < SUMMARY_TRIGGER_TURNS:
        return

    conv = db.execute(
        select(Conversation).where(Conversation.tenant_id == str(tenant_id), Conversation.id == conversation_id)
    ).scalar_one_or_none()
    if not conv:
        return

    # summarize everything except the last buffer
    all_msgs = db.execute(
        select(ChatMessage)
        .where(ChatMessage.tenant_id == str(tenant_id), ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()

    keep = DEFAULT_BUFFER_TURNS
    to_summarize = all_msgs[:-keep] if len(all_msgs) > keep else []
    if not to_summarize:
        return

    transcript = "\n".join([f"{m.role.upper()}: {m.content}" for m in to_summarize])

    try:
        from agent.utils.openai_wrapper import chat_completion
    except Exception:
        return

    messages = [
        {
            "role": "system",
            "content": "Summarize this QA conversation into concise bullet points capturing decisions, constraints, open questions, and important context. No fluff."
        },
        {"role": "user", "content": transcript},
    ]

    try:
        resp = chat_completion(
            messages=messages,
            model="gpt-4o-mini",
            temperature=0.2,
            service_name="qa-agent-chat-summary",
        )
        summary = (resp.choices[0].message.content or "").strip()
        if summary:
            conv.summary = summary
            conv.updated_at = _now()
            db.commit()
    except Exception:
        return


def generate_reply(
    db: Session,
    tenant_id: str,
    account_id: str | None,
    conversation_id: str,
    user_message: str,
    retrieved_chunks_text: str | None = None,
) -> str:
    """
    Buffer memory = last N messages + rolling summary.
    """
    conv = db.get(Conversation, conversation_id)
    summary = (conv.summary or "").strip() if conv else ""

    recent = get_recent_messages(db, tenant_id, conversation_id, limit=DEFAULT_BUFFER_TURNS)

    system = (
        "You are a QA Orchestration Assistant.\n"
        "- Ask clarifying questions when requirements are ambiguous.\n"
        "- Produce structured, readable answers (headings + bullets).\n"
        "- Be practical: include test ideas, risks, and next steps.\n"
        "- Do NOT invent product requirements.\n"
    )

    if summary:
        system += "\nConversation summary so far:\n" + summary

    prompt_msgs = [{"role": "system", "content": system}]

    if retrieved_chunks_text:
        prompt_msgs.append(
            {
                "role": "system",
                "content": "Relevant spec/context excerpts:\n" + retrieved_chunks_text,
            }
        )

    # add recent history
    for m in recent:
        prompt_msgs.append({"role": m.role, "content": m.content})

    # add current user msg
    prompt_msgs.append({"role": "user", "content": user_message})

    from agent.utils.openai_wrapper import chat_completion

    resp = chat_completion(
        messages=prompt_msgs,
        model="gpt-4o-mini",
        temperature=0.3,
        service_name="qa-agent-chat",
    )
    answer = (resp.choices[0].message.content or "").strip()
    return answer
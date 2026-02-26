# agent/ticket_providers.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, Any


class NotConfiguredError(RuntimeError):
    pass


@dataclass
class Ticket:
    id: str
    title: str
    description: str
    attachments: list[dict[str, Any]]


class TicketProvider(Protocol):
    def get_ticket(self, ticket_id: str) -> Ticket: ...
    def search(self, query: str, filters: dict[str, Any] | None = None) -> list[Ticket]: ...
    def get_attachments(self, ticket_id: str) -> list[dict[str, Any]]: ...


class JiraProvider:
    def __init__(self):
        raise NotConfiguredError("Jira provider not configured yet.")

class AzureDevOpsProvider:
    def __init__(self):
        raise NotConfiguredError("Azure DevOps provider not configured yet.")
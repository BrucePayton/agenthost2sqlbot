from typing import Protocol

from fastapi import Request

from app.auth.models import IdentityContext


class IdentityProvider(Protocol):
    async def resolve(self, request: Request) -> IdentityContext:
        raise NotImplementedError

    async def resolve_bootstrap_identity(self) -> IdentityContext | None:
        raise NotImplementedError


class MockIdentityProvider:
    def __init__(self, user_id: str, subject: str, display_name: str) -> None:
        self.identity = IdentityContext(user_id, subject, display_name)

    async def resolve(self, request: Request) -> IdentityContext:
        return self.identity

    async def resolve_bootstrap_identity(self) -> IdentityContext:
        return self.identity

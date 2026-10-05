from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest

from tests.test_workspaces import write_workspace

TEMPLATE_INSTRUCTIONS = "# Template instructions\n\n照模板给的那份。\n"


class InstructionsIdentityProvider:
    async def resolve(self, request):
        from app.auth.models import IdentityContext

        identities = {
            "owner": IdentityContext("owner", "owner", "Owner"),
            "stranger": IdentityContext("stranger", "stranger", "Stranger"),
        }
        return identities[request.headers.get("X-Test-User", "owner")]

    async def resolve_bootstrap_identity(self):
        from app.auth.models import IdentityContext

        return IdentityContext("owner", "owner", "Owner")


@asynccontextmanager
async def instructions_client(settings_factory) -> AsyncIterator[httpx.AsyncClient]:
    """两个 personal workspace：`personal` 归 owner，`stranger-ws` 归另一个人。"""
    from app.auth.models import WorkspaceRole
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner", "stranger-ws": "owner"},
    )
    template = write_workspace(settings.workspaces_root, "personal", skills=())
    (template / "CLAUDE.md").write_text(TEMPLATE_INSTRUCTIONS, encoding="utf-8")
    write_workspace(settings.workspaces_root, "stranger-ws", skills=())
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        identity_provider=InstructionsIdentityProvider(),
    )
    async with app.router.lifespan_context(app):
        now = datetime.now(UTC)
        async with app.state.services.database.session() as db:
            db.add(
                UserRecord(
                    id="stranger",
                    external_subject="stranger",
                    display_name="Stranger",
                    provider="mock",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.flush()
            db.add(
                WorkspaceMemberRecord(
                    workspace_id="stranger-ws",
                    user_id="stranger",
                    role=WorkspaceRole.OWNER.value,
                    created_at=now,
                )
            )
            owned_by_owner = await db.get(
                WorkspaceMemberRecord, ("stranger-ws", "owner")
            )
            assert owned_by_owner is not None
            await db.delete(owned_by_owner)
            await db.flush()
            stranger_workspace = await db.get(WorkspaceRecord, "stranger-ws")
            assert stranger_workspace is not None
            stranger_workspace.owner_user_id = "stranger"
            stranger_workspace.template_id = "stranger-ws"
            stranger_workspace.kind = "personal"
            await db.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Test-User": "owner"},
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_get_returns_the_effective_instructions(settings_factory) -> None:
    """无覆盖时也要返回默认全文，编辑器打开即可用。"""
    async with instructions_client(settings_factory) as client:
        response = await client.get("/api/workspaces/personal/instructions")

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == TEMPLATE_INSTRUCTIONS
    assert body["source"] == "template"
    assert body["max_bytes"] == 16384
    assert body["size_bytes"] == len(TEMPLATE_INSTRUCTIONS.encode("utf-8"))
    assert body["content_hash"].startswith("sha256:")
    assert body["updated_at"] is None
    assert body["updated_by_name"] is None


@pytest.mark.asyncio
async def test_default_endpoint_returns_the_resolved_default(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# mine"},
        )
        response = await client.get("/api/workspaces/personal/instructions/default")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"content", "source", "content_hash", "size_bytes"}
    assert body["content"] == TEMPLATE_INSTRUCTIONS
    assert body["source"] == "template"


@pytest.mark.asyncio
async def test_put_then_get_returns_the_custom_copy(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        saved = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# only mine\n"},
        )
        fetched = await client.get("/api/workspaces/personal/instructions")

    assert saved.status_code == 200
    body = saved.json()
    assert body["content"] == "# only mine\n"
    assert body["source"] == "custom"
    assert body["updated_by_name"] == "Owner"
    assert body["updated_at"] is not None
    assert fetched.json() == body


@pytest.mark.asyncio
async def test_put_honours_a_matching_expected_hash(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        first = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# first"},
        )
        second = await client.put(
            "/api/workspaces/personal/instructions",
            json={
                "content": "# second",
                "expected_hash": first.json()["content_hash"],
            },
        )

    assert second.status_code == 200
    assert second.json()["content"] == "# second"


@pytest.mark.asyncio
async def test_first_save_may_send_a_null_expected_hash(settings_factory) -> None:
    """编辑器从默认态首次保存时没有 hash 可带，显式 null 也要放行。"""
    async with instructions_client(settings_factory) as client:
        response = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# first", "expected_hash": None},
        )

    assert response.status_code == 200
    assert response.json()["source"] == "custom"


@pytest.mark.asyncio
async def test_stale_expected_hash_conflicts(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# first"},
        )
        response = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# second", "expected_hash": "sha256:" + "0" * 64},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "instructions_changed"


@pytest.mark.asyncio
async def test_delete_restores_the_default(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# only mine"},
        )
        deleted = await client.delete("/api/workspaces/personal/instructions")
        restored = await client.get("/api/workspaces/personal/instructions")

    assert deleted.status_code == 204
    assert deleted.content == b""
    body = restored.json()
    assert body["content"] == TEMPLATE_INSTRUCTIONS
    assert body["source"] == "template"
    assert body["updated_at"] is None
    assert body["updated_by_name"] is None


@pytest.mark.asyncio
async def test_delete_checks_the_expected_hash(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        saved = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# only mine"},
        )
        stale = await client.request(
            "DELETE",
            "/api/workspaces/personal/instructions",
            json={"expected_hash": "sha256:" + "0" * 64},
        )
        current = await client.request(
            "DELETE",
            "/api/workspaces/personal/instructions",
            json={"expected_hash": saved.json()["content_hash"]},
        )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "instructions_changed"
    assert current.status_code == 204


@pytest.mark.asyncio
async def test_oversized_put_is_rejected(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        response = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "x" * 16385},
        )
        unchanged = await client.get("/api/workspaces/personal/instructions")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "instructions_too_large"
    assert unchanged.json()["source"] == "template"


@pytest.mark.asyncio
async def test_nul_bytes_are_rejected(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        response = await client.put(
            "/api/workspaces/personal/instructions",
            json={"content": "# oops\x00"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "instructions_invalid"


@pytest.mark.asyncio
async def test_another_users_workspace_is_not_found(settings_factory) -> None:
    """未授权按仓库惯例返回 404 而非 403（防枚举）。"""
    async with instructions_client(settings_factory) as client:
        read = await client.get("/api/workspaces/stranger-ws/instructions")
        read_default = await client.get(
            "/api/workspaces/stranger-ws/instructions/default"
        )
        written = await client.put(
            "/api/workspaces/stranger-ws/instructions",
            json={"content": "# not mine"},
        )
        deleted = await client.delete("/api/workspaces/stranger-ws/instructions")

    for response in (read, read_default, written, deleted):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "workspace_not_found"


@pytest.mark.asyncio
async def test_unknown_workspace_is_not_found(settings_factory) -> None:
    async with instructions_client(settings_factory) as client:
        response = await client.get("/api/workspaces/does-not-exist/instructions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workspace_not_found"


def test_response_source_matches_the_service_literal() -> None:
    """schema 的 source 取值必须跟服务层的 InstructionSource 同步。"""
    from typing import get_args

    from app.api.schemas import InstructionsDefaultOut
    from app.instructions.service import InstructionSource

    field = InstructionsDefaultOut.model_fields["source"]
    assert set(get_args(field.annotation)) == set(get_args(InstructionSource))

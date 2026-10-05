import io
import json
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest

from tests.test_workspaces import write_workspace

VALID_SKILL_TEXT = """---
name: review-changes
description: Review changes
---
# Review changes
"""


class SkillApiIdentityProvider:
    async def resolve(self, request):
        from app.auth.models import IdentityContext

        identities = {
            "manager": IdentityContext("manager", "manager", "Manager"),
            "member": IdentityContext("member", "member", "Member"),
            "outsider": IdentityContext("outsider", "outsider", "Outsider"),
            "unregistered": IdentityContext(
                "unregistered", "unregistered", "Unregistered"
            ),
            "second-owner": IdentityContext(
                "second-owner", "second-owner", "Second Owner"
            ),
        }
        return identities[request.headers.get("X-Test-User", "manager")]

    async def resolve_bootstrap_identity(self):
        from app.auth.models import IdentityContext

        return IdentityContext("manager", "manager", "Manager")


@asynccontextmanager
async def skill_api_client(settings_factory) -> AsyncIterator[httpx.AsyncClient]:
    from app.auth.models import WorkspaceRole
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={
            "personal": "owner",
            "team": "owner",
            "legacy-team": "owner",
        },
        max_skill_bundle_size_mb=1,
    )
    write_workspace(settings.workspaces_root, "personal", skills=())
    write_workspace(settings.workspaces_root, "team", skills=())
    write_workspace(settings.workspaces_root, "legacy-team", skills=())
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        identity_provider=SkillApiIdentityProvider(),
    )
    async with app.router.lifespan_context(app):
        now = datetime.now(UTC)
        async with app.state.services.database.session() as db:
            db.add_all(
                [
                    UserRecord(
                        id="member",
                        external_subject="member",
                        display_name="Member",
                        provider="mock",
                        created_at=now,
                        updated_at=now,
                    ),
                    UserRecord(
                        id="outsider",
                        external_subject="outsider",
                        display_name="Outsider",
                        provider="mock",
                        created_at=now,
                        updated_at=now,
                    ),
                    UserRecord(
                        id="second-owner",
                        external_subject="second-owner",
                        display_name="Second Owner",
                        provider="mock",
                        created_at=now,
                        updated_at=now,
                    ),
                    WorkspaceMemberRecord(
                        workspace_id="team",
                        user_id="member",
                        role=WorkspaceRole.MEMBER.value,
                        created_at=now,
                    ),
                    WorkspaceMemberRecord(
                        workspace_id="legacy-team",
                        user_id="member",
                        role=WorkspaceRole.MEMBER.value,
                        created_at=now,
                    ),
                    WorkspaceMemberRecord(
                        workspace_id="team",
                        user_id="second-owner",
                        role=WorkspaceRole.OWNER.value,
                        created_at=now,
                    ),
                ]
            )
            await db.flush()
            team = await db.get(WorkspaceRecord, "team")
            assert team is not None
            manager_membership = await db.get(
                WorkspaceMemberRecord, ("team", "manager")
            )
            assert manager_membership is not None
            member_membership = await db.get(
                WorkspaceMemberRecord, ("team", "member")
            )
            assert member_membership is not None
            await db.delete(manager_membership)
            await db.delete(member_membership)
            await db.flush()
            team.kind = "personal"
            team.owner_user_id = "second-owner"
            await db.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Test-User": "manager"},
        ) as client:
            yield client


def _archive(*entries: tuple[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    return raw.getvalue()


def _directory_parts(
    source_name: str,
    skill_markdown: bytes,
    supporting: list[tuple[str, bytes]] | None = None,
) -> list[tuple[str, tuple[str, bytes, str]]]:
    parts = [
        (
            "files",
            (f"{source_name}/SKILL.md", skill_markdown, "text/markdown"),
        )
    ]
    for path, content in supporting or []:
        parts.append(
            (
                "files",
                (f"{source_name}/{path}", content, "application/octet-stream"),
            )
        )
    return parts


def _multipart_body(boundary: bytes, filename: str, content: bytes) -> bytes:
    return b"".join(
        (
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="archive"; filename="'
            + filename.encode("ascii")
            + b'"\r\n',
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            b"\r\n--" + boundary + b"--\r\n",
        )
    )


@asynccontextmanager
async def streamed_skill_api_app(settings_factory):
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner"},
        max_skill_bundle_size_mb=1,
    )
    write_workspace(settings.workspaces_root, "personal", skills=())
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        identity_provider=SkillApiIdentityProvider(),
    )
    async with app.router.lifespan_context(app):
        yield app


async def _asgi_import(
    app, chunks: list[bytes], content_type: str
) -> tuple[int, dict[str, object], int]:
    messages: list[dict[str, object]] = []
    position = 0

    async def receive() -> dict[str, object]:
        nonlocal position
        if position >= len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[position]
        position += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": position < len(chunks),
        }

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/workspaces/personal/skills/import",
            "raw_path": b"/api/workspaces/personal/skills/import",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", content_type.encode("ascii")),
                (b"x-test-user", b"manager"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    response = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(response["status"]), json.loads(body), position


async def _import_archive(
    client: httpx.AsyncClient,
    path: str,
    skill_text: str,
    *,
    on_conflict: str = "fail",
    expected_hash: str | None = None,
    target_name: str | None = None,
) -> httpx.Response:
    data = {"on_conflict": on_conflict}
    if expected_hash is not None:
        data["expected_hash"] = expected_hash
    if target_name is not None:
        data["target_name"] = target_name
    return await client.post(
        path,
        data=data,
        files={
            "archive": (
                "skill.zip",
                _archive(("SKILL.md", skill_text.encode())),
                "application/zip",
            )
        },
    )


def _skill_text(name: str, description: str) -> str:
    return f"""---
name: {name}
description: {description}
---
# {description}
"""


async def _asgi_post_without_body_read(
    app,
    *,
    path: str,
    user: str,
    content_type: str,
    body: bytes,
) -> tuple[int, dict[str, object], int]:
    messages: list[dict[str, object]] = []
    body_reads = 0

    async def receive() -> dict[str, object]:
        nonlocal body_reads
        body_reads += 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", content_type.encode()),
                (b"x-test-user", user.encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    response = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(response["status"]), json.loads(response_body), body_reads


@pytest.mark.asyncio
async def test_skill_import_stops_reading_asgi_chunks_when_file_exceeds_limit(
    settings_factory,
) -> None:
    limit = 1024 * 1024
    boundary = b"skill-boundary"
    header = b"".join(
        (
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="archive"; filename="large.skill"\r\n',
            b"Content-Type: application/octet-stream\r\n\r\n",
        )
    )
    chunks = [
        header,
        b"x" * limit,
        b"x",
        b"\r\n--" + boundary + b"--\r\n",
    ]

    async with streamed_skill_api_app(settings_factory) as app:
        status_code, payload, chunks_read = await _asgi_import(
            app, chunks, f"multipart/form-data; boundary={boundary.decode()}"
        )

    error = payload["error"]
    assert isinstance(error, dict)
    assert (status_code, error["code"]) == (413, "skill_bundle_too_large")
    assert error["request_id"]
    assert chunks_read == 3


@pytest.mark.asyncio
async def test_skill_import_accepts_file_exactly_at_compressed_limit(
    settings_factory,
) -> None:
    limit = 1024 * 1024
    oversized = _archive(
        ("SKILL.md", VALID_SKILL_TEXT.encode()), ("filler.bin", b"x" * limit)
    )
    archive = _archive(
        ("SKILL.md", VALID_SKILL_TEXT.encode()),
        ("filler.bin", b"x" * (limit - (len(oversized) - limit))),
    )
    assert len(archive) == limit
    boundary = b"exact-limit"

    async with streamed_skill_api_app(settings_factory) as app:
        status_code, payload, chunks_read = await _asgi_import(
            app,
            [_multipart_body(boundary, "exact.zip", archive)],
            f"multipart/form-data; boundary={boundary.decode()}",
        )

    assert status_code == 201
    assert payload["status"] == "created"
    assert payload["skill"]["workspace_id"] == "personal"
    assert chunks_read == 1


@pytest.mark.asyncio
async def test_skill_import_stops_reading_after_closing_multipart_boundary(
    settings_factory,
) -> None:
    boundary = b"closed"
    archive = _archive(("SKILL.md", VALID_SKILL_TEXT.encode()))
    chunks = [
        _multipart_body(boundary, "valid.zip", archive),
        b"x" * (1024 * 1024),
        b"y" * (1024 * 1024),
    ]

    async with streamed_skill_api_app(settings_factory) as app:
        status_code, payload, chunks_read = await _asgi_import(
            app,
            chunks,
            f"multipart/form-data; boundary={boundary.decode()}",
        )

    assert status_code == 201
    assert payload["status"] == "created"
    assert payload["skill"]["workspace_id"] == "personal"
    assert chunks_read == 1


@pytest.mark.asyncio
async def test_skill_import_rejects_malformed_multipart_with_bundle_envelope(
    settings_factory,
) -> None:
    boundary = b"malformed"
    malformed = b"".join(
        (
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="archive"; filename="bad.zip"\r\n',
            b"Content-Type: application/octet-stream\r\n\r\n",
            b"not terminated",
        )
    )

    async with streamed_skill_api_app(settings_factory) as app:
        status_code, payload, _ = await _asgi_import(
            app,
            [malformed],
            f"multipart/form-data; boundary={boundary.decode()}",
        )

    error = payload["error"]
    assert isinstance(error, dict)
    assert (status_code, error["code"]) == (422, "invalid_skill_bundle")
    assert error["request_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prefix_chunks", "expected_chunks_read"),
    [
        ([b"--bounded\r\n", b"X-Large: " + b"x" * (70 * 1024)], 2),
        (
            [
                b"--bounded\r\n",
                b"".join(f"X-{index}: value\r\n".encode() for index in range(40)),
            ],
            2,
        ),
        (
            [
                b"--bounded\r\n",
                b"".join(
                    f"X-{index}: ".encode() + b"x" * 2048 + b"\r\n"
                    for index in range(20)
                ),
            ],
            2,
        ),
        ([b"p" * (70 * 1024)], 1),
    ],
    ids=["individual-header", "header-count", "aggregate-headers", "preamble"],
)
async def test_skill_import_bounds_multipart_overhead_and_stops_before_tail(
    settings_factory,
    prefix_chunks: list[bytes],
    expected_chunks_read: int,
) -> None:
    boundary = b"bounded"
    archive = _archive(("SKILL.md", VALID_SKILL_TEXT.encode()))
    valid_part = _multipart_body(boundary, "valid.zip", archive)
    chunks = [*prefix_chunks, valid_part, b"unconsumed-tail"]

    async with streamed_skill_api_app(settings_factory) as app:
        status_code, payload, chunks_read = await _asgi_import(
            app,
            chunks,
            f"multipart/form-data; boundary={boundary.decode()}",
        )

    assert (status_code, payload["error"]["code"]) == (
        413,
        "skill_bundle_too_large",
    )
    assert chunks_read == expected_chunks_read


@pytest.mark.asyncio
async def test_personal_skill_enable_and_delete_succeed(settings_factory) -> None:
    async with skill_api_client(settings_factory) as manager_client:
        initial_workspaces = await manager_client.get("/api/workspaces")
        assert initial_workspaces.status_code == 200
        assert {item["id"]: item["skill_count"] for item in initial_workspaces.json()} == {
            "personal": 0,
            "legacy-team": 0,
        }

        created = await _import_archive(
            manager_client,
            "/api/workspaces/personal/skills/import",
            VALID_SKILL_TEXT,
        )
        assert created.status_code == 201
        skill = created.json()["skill"]
        assert skill["description"] == "Review changes"
        assert skill["enabled"] is True
        assert "content_blob" not in created.text

        listed = await manager_client.get("/api/workspaces/personal/skills")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["personal"]] == [skill["id"]]

        disabled = await manager_client.patch(
            f"/api/workspaces/personal/skills/{skill['id']}/enabled",
            json={"expected_hash": skill["bundle_hash"], "enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        counts_after_disable = await manager_client.get("/api/workspaces")
        assert {
            item["id"]: item["skill_count"] for item in counts_after_disable.json()
        } == {
            "personal": 0,
            "legacy-team": 0,
        }
        enabled = await manager_client.patch(
            f"/api/workspaces/personal/skills/{skill['id']}/enabled",
            json={"expected_hash": skill["bundle_hash"], "enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        counts_after_enable = await manager_client.get("/api/workspaces")
        assert {
            item["id"]: item["skill_count"] for item in counts_after_enable.json()
        } == {
            "personal": 1,
            "legacy-team": 0,
        }

        detail = await manager_client.get(f"/api/skills/{skill['id']}")
        assert detail.status_code == 200
        assert detail.json()["content"] == VALID_SKILL_TEXT
        assert detail.json()["files"] == []

        archived = await manager_client.request(
            "DELETE",
            f"/api/workspaces/personal/skills/{skill['id']}",
            json={"expected_hash": enabled.json()["bundle_hash"]},
        )
        assert archived.status_code == 204
        counts_after_archive = await manager_client.get("/api/workspaces")
        assert {item["id"]: item["skill_count"] for item in counts_after_archive.json()} == {
            "personal": 0,
            "legacy-team": 0,
        }


@pytest.mark.asyncio
async def test_skill_api_import_validates_filename_archive_and_compressed_size(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as manager_client:
        invalid_name = await manager_client.post(
            "/api/workspaces/personal/skills/import",
            files={"archive": ("skill.tar", b"irrelevant", "application/octet-stream")},
        )
        assert (invalid_name.status_code, invalid_name.json()["error"]["code"]) == (
            422,
            "invalid_skill_bundle",
        )

        invalid_path = await manager_client.post(
            "/api/workspaces/personal/skills/import",
            files={
                "archive": (
                    "skill.zip",
                    _archive(("../SKILL.md", VALID_SKILL_TEXT.encode())),
                    "application/zip",
                )
            },
        )
        assert (invalid_path.status_code, invalid_path.json()["error"]["code"]) == (
            422,
            "invalid_skill_bundle",
        )

        imported = await manager_client.post(
            "/api/workspaces/personal/skills/import",
            files={
                "archive": (
                    "review.zip",
                    _archive(("SKILL.md", VALID_SKILL_TEXT.encode())),
                    "application/zip",
                )
            },
        )
        assert imported.status_code == 201
        assert imported.json()["status"] == "created"
        assert imported.json()["skill"]["workspace_id"] == "personal"

        duplicate = await manager_client.post(
            "/api/workspaces/personal/skills/import",
            files=[
                (
                    "archive",
                    ("first.zip", _archive(("SKILL.md", VALID_SKILL_TEXT.encode()))),
                ),
                (
                    "archive",
                    ("second.zip", _archive(("SKILL.md", VALID_SKILL_TEXT.encode()))),
                ),
            ],
        )
        assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (
            422,
            "invalid_skill_bundle",
        )

        too_large = await manager_client.post(
            "/api/workspaces/personal/skills/import",
            files={
                "archive": (
                    "large.skill",
                    b"x" * (1024 * 1024 + 1),
                    "application/octet-stream",
                )
            },
        )
        assert (too_large.status_code, too_large.json()["error"]["code"]) == (
            413,
            "skill_bundle_too_large",
        )
        catalog = (
            await manager_client.get("/api/workspaces/personal/skills")
        ).json()
        assert [item["id"] for item in catalog["personal"]] == [
            imported.json()["skill"]["id"]
        ]


@pytest.mark.asyncio
async def test_unscoped_aliases_fail_closed_for_team_skills(settings_factory) -> None:
    from app.auth.models import IdentityContext

    async with skill_api_client(settings_factory) as manager_client:
        skill = await manager_client._transport.app.state.services.skills.create(
            "legacy-team",
            IdentityContext("manager", "manager", "Manager"),
            VALID_SKILL_TEXT,
        )
        for user in ("manager", "member", "outsider"):
            headers = {"X-Test-User": user}
            responses = (
                await manager_client.get(f"/api/skills/{skill.id}", headers=headers),
                await manager_client.patch(
                    f"/api/skills/{skill.id}",
                    json={"expected_hash": skill.bundle_hash, "enabled": False},
                    headers=headers,
                ),
                await manager_client.request(
                    "DELETE",
                    f"/api/skills/{skill.id}",
                    json={"expected_hash": skill.bundle_hash},
                    headers=headers,
                ),
            )
            assert [
                (response.status_code, response.json()["error"]["code"])
                for response in responses
            ] == [(404, "skill_not_found")] * 3


@pytest.mark.asyncio
async def test_directory_import_api_creates_enabled_bundle_and_is_idempotent(
    settings_factory,
) -> None:
    supporting = [("references/policy.bin", b"\x00\xff")]
    async with skill_api_client(settings_factory) as manager_client:
        created = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts(
                "review-folder", VALID_SKILL_TEXT.encode(), supporting
            ),
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["status"] == "created"
        assert payload["skill"]["enabled"] is True
        assert payload["skill"]["origin"] == {
            "type": "browser_directory",
            "source_name": "review-folder",
        }

        repeated = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts(
                "review-folder", VALID_SKILL_TEXT.encode(), supporting
            ),
        )
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "already_imported"
        assert repeated.json()["skill"]["id"] == payload["skill"]["id"]

        detail = await manager_client.get(
            f"/api/skills/{payload['skill']['id']}"
        )
        assert detail.status_code == 200
        assert detail.json()["files"] == [
            {
                "path": "references/policy.bin",
                "mime_type": "application/octet-stream",
                "size_bytes": 2,
                "sha256": "sha256:06eb7d6a69ee19e5fbdf749018d3d2abfa04bcbd1365db312eb86dc7169389b8",
            }
        ]


@pytest.mark.asyncio
async def test_directory_import_api_resolves_conflict_by_overwrite_or_rename(
    settings_factory,
) -> None:
    changed_text = VALID_SKILL_TEXT.replace("Review changes", "Review safely")
    third_text = VALID_SKILL_TEXT.replace("Review changes", "Review thoroughly")
    async with skill_api_client(settings_factory) as manager_client:
        created = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts("review-folder", VALID_SKILL_TEXT.encode()),
        )
        original = created.json()["skill"]
        enabled_response = await manager_client.patch(
            f"/api/skills/{original['id']}",
            json={"expected_hash": original["bundle_hash"], "enabled": True},
        )
        enabled = enabled_response.json()

        conflict = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts("changed-folder", changed_text.encode()),
        )
        assert (conflict.status_code, conflict.json()["error"]["code"]) == (
            409,
            "skill_import_conflict",
        )
        details = conflict.json()["error"]["details"]
        assert set(details) == {
            "skill_id",
            "existing_hash",
            "incoming_hash",
            "incoming_name",
        }
        assert details["skill_id"] == original["id"]
        assert details["existing_hash"] == enabled["bundle_hash"]
        assert details["incoming_hash"] != enabled["bundle_hash"]
        assert details["incoming_hash"].startswith("sha256:")
        assert len(details["incoming_hash"]) == 71
        assert details["incoming_name"] == "review-changes"

        overwritten = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={
                "on_conflict": "overwrite",
                "expected_hash": enabled["bundle_hash"],
            },
            files=_directory_parts("changed-folder", changed_text.encode()),
        )
        assert overwritten.status_code == 200
        overwritten_payload = overwritten.json()
        assert overwritten_payload["status"] == "overwritten"
        assert overwritten_payload["skill"]["id"] == original["id"]
        assert overwritten_payload["skill"]["enabled"] is True
        assert overwritten_payload["skill"]["description"] == "Review safely"

        stale = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={
                "on_conflict": "overwrite",
                "expected_hash": enabled["bundle_hash"],
            },
            files=_directory_parts("third-folder", third_text.encode()),
        )
        assert (stale.status_code, stale.json()["error"]["code"]) == (
            409,
            "skill_changed",
        )

        renamed = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "rename", "target_name": "review-copy"},
            files=_directory_parts("third-folder", third_text.encode()),
        )
        assert renamed.status_code == 201
        renamed_payload = renamed.json()
        assert renamed_payload["status"] == "renamed"
        assert renamed_payload["skill"]["id"] != original["id"]
        assert renamed_payload["skill"]["name"] == "review-copy"
        assert renamed_payload["skill"]["enabled"] is True


@pytest.mark.asyncio
async def test_directory_import_api_rejects_invalid_root_and_total_size(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as manager_client:
        invalid_root = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "fail"},
            files={
                "files": ("SKILL.md", VALID_SKILL_TEXT.encode(), "text/markdown")
            },
        )
        assert (
            invalid_root.status_code,
            invalid_root.json()["error"]["code"],
        ) == (422, "invalid_skill_bundle")

        too_large = await manager_client.post(
            "/api/workspaces/personal/skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts(
                "large",
                VALID_SKILL_TEXT.encode(),
                [("large.bin", b"x" * (1024 * 1024))],
            ),
        )
        assert (too_large.status_code, too_large.json()["error"]["code"]) == (
            413,
            "skill_bundle_too_large",
        )
        listed = await manager_client.get("/api/workspaces/personal/skills")
        assert listed.json()["personal"] == []
        assert listed.json()["effective_count"] == 0


@pytest.mark.asyncio
async def test_directory_import_api_denies_member_and_outsider(settings_factory) -> None:
    async with skill_api_client(settings_factory) as manager_client:
        for user in ("member", "outsider"):
            denied = await manager_client.post(
                "/api/workspaces/team/skills/import-directory",
                headers={"X-Test-User": user},
                data={"on_conflict": "fail"},
                files=_directory_parts("review-folder", VALID_SKILL_TEXT.encode()),
            )
            assert (denied.status_code, denied.json()["error"]["code"]) == (
                404,
                "workspace_not_found",
            )


@pytest.mark.asyncio
async def test_directory_import_api_authorizes_before_reading_body(
    settings_factory,
) -> None:
    boundary = b"authorization-order"
    body = b"".join(
        (
            b"--" + boundary + b"\r\n",
            b'Content-Disposition: form-data; name="files"; filename="skill/SKILL.md"\r\n\r\n',
            VALID_SKILL_TEXT.encode(),
            b"\r\n--" + boundary + b"--\r\n",
        )
    )
    async with skill_api_client(settings_factory) as manager_client:
        app = manager_client._transport.app
        body_reads = 0
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            nonlocal body_reads
            body_reads += 1
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/workspaces/team/skills/import-directory",
                "raw_path": b"/api/workspaces/team/skills/import-directory",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"content-type", b"multipart/form-data; boundary=" + boundary),
                    (b"x-test-user", b"member"),
                ],
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    response = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert body_reads == 0
    assert int(response["status"]) == 404
    assert json.loads(response_body)["error"]["code"] == "workspace_not_found"


@pytest.mark.asyncio
async def test_catalog_groups_global_and_personal_and_reports_effective_count(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as client:
        global_created = await _import_archive(
            client,
            "/api/admin/global-skills/import",
            _skill_text("global-review", "Global review"),
        )
        personal_created = await _import_archive(
            client,
            "/api/workspaces/personal/skills/import",
            _skill_text("personal-review", "Personal review"),
        )
        assert global_created.status_code == 201
        assert personal_created.status_code == 201

        response = await client.get("/api/workspaces/personal/skills")
        workspaces = await client.get("/api/workspaces")

    assert response.status_code == 200
    payload = response.json()
    assert payload["changes_apply_to"] == "new_sessions"
    assert payload["effective_count"] == 2
    assert [item["scope"] for item in payload["global"]] == ["global"]
    assert [item["scope"] for item in payload["personal"]] == ["workspace"]
    assert payload["global"][0]["enabled"] is True
    assert payload["global"][0]["workspace_id"] is None
    assert payload["personal"][0]["workspace_id"] == "personal"
    assert payload["personal"][0]["version_id"]
    assert payload["personal"][0]["version_no"] == 1
    assert {item["id"]: item["skill_count"] for item in workspaces.json()} == {
        "personal": 2,
        "legacy-team": 0,
    }


@pytest.mark.asyncio
async def test_personal_archive_upload_defaults_enabled(settings_factory) -> None:
    async with skill_api_client(settings_factory) as client:
        response = await client.post(
            "/api/workspaces/personal/skills/import",
            files={
                "archive": (
                    "personal.zip",
                    _archive(
                        (
                            "SKILL.md",
                            _skill_text("personal-archive", "Personal archive").encode(),
                        )
                    ),
                    "application/zip",
                )
            },
        )

    assert response.status_code == 201
    assert response.json()["status"] == "created"
    assert response.json()["skill"]["enabled"] is True


@pytest.mark.asyncio
async def test_personal_directory_upload_defaults_enabled(settings_factory) -> None:
    async with skill_api_client(settings_factory) as client:
        response = await client.post(
            "/api/workspaces/personal/skills/import-directory",
            files=_directory_parts(
                "personal-directory",
                _skill_text("personal-directory", "Personal directory").encode(),
            ),
        )

    assert response.status_code == 201
    assert response.json()["status"] == "created"
    assert response.json()["skill"]["enabled"] is True


@pytest.mark.asyncio
async def test_global_toggle_is_workspace_specific_and_requires_personal_owner(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as client:
        created = await _import_archive(
            client,
            "/api/admin/global-skills/import",
            _skill_text("toggle-global", "Toggle global"),
        )
        skill = created.json()["skill"]

        disabled = await client.put(
            f"/api/workspaces/personal/global-skills/{skill['id']}/setting",
            json={"enabled": False},
        )
        personal_catalog = await client.get("/api/workspaces/personal/skills")
        second_catalog = await client.get(
            "/api/workspaces/team/skills", headers={"X-Test-User": "second-owner"}
        )
        denied = await client.put(
            f"/api/workspaces/team/global-skills/{skill['id']}/setting",
            json={"enabled": False},
            headers={"X-Test-User": "outsider"},
        )

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert personal_catalog.json()["global"][0]["enabled"] is False
    assert personal_catalog.json()["effective_count"] == 0
    assert second_catalog.json()["global"][0]["enabled"] is True
    assert second_catalog.json()["effective_count"] == 1
    assert (denied.status_code, denied.json()["error"]["code"]) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
async def test_global_archive_upload_rejects_unregistered_user_before_reading_body(
    settings_factory,
) -> None:
    boundary = "admin-archive-auth"
    async with skill_api_client(settings_factory) as client:
        status_code, payload, body_reads = await _asgi_post_without_body_read(
            client._transport.app,
            path="/api/admin/global-skills/import",
            user="unregistered",
            content_type=f"multipart/form-data; boundary={boundary}",
            body=b"body must remain unread",
        )

    assert body_reads == 0
    assert (status_code, payload["error"]["code"]) == (404, "skill_not_found")


@pytest.mark.asyncio
async def test_global_directory_upload_rejects_unregistered_user_before_reading_body(
    settings_factory,
) -> None:
    boundary = "admin-directory-auth"
    async with skill_api_client(settings_factory) as client:
        status_code, payload, body_reads = await _asgi_post_without_body_read(
            client._transport.app,
            path="/api/admin/global-skills/import-directory",
            user="unregistered",
            content_type=f"multipart/form-data; boundary={boundary}",
            body=b"body must remain unread",
        )

    assert body_reads == 0
    assert (status_code, payload["error"]["code"]) == (404, "skill_not_found")


@pytest.mark.asyncio
async def test_registered_user_can_import_inspect_and_archive_global_skill(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as client:
        headers = {"X-Test-User": "member"}
        created = await client.post(
            "/api/admin/global-skills/import-directory",
            headers=headers,
            files=_directory_parts(
                "shared-directory",
                _skill_text("shared-directory", "Shared directory").encode(),
            ),
        )
        skill = created.json()["skill"]
        detail = await client.get(
            f"/api/admin/global-skills/{skill['id']}", headers=headers
        )
        deleted = await client.request(
            "DELETE",
            f"/api/admin/global-skills/{skill['id']}",
            headers=headers,
            json={"expected_hash": skill["bundle_hash"]},
        )
        after_delete = await client.get(
            f"/api/admin/global-skills/{skill['id']}", headers=headers
        )

    assert created.status_code == 201
    assert created.json()["status"] == "created"
    assert skill["scope"] == "global"
    assert skill["enabled"] is True
    assert detail.status_code == 200
    assert deleted.status_code == 204
    assert (after_delete.status_code, after_delete.json()["error"]["code"]) == (
        404,
        "skill_not_found",
    )


@pytest.mark.asyncio
async def test_registered_user_global_directory_conflict_status_mapping(
    settings_factory,
) -> None:
    original_text = _skill_text("admin-conflict", "Original")
    changed_text = _skill_text("admin-conflict", "Changed")
    async with skill_api_client(settings_factory) as client:
        created = await client.post(
            "/api/admin/global-skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts("admin-original", original_text.encode()),
        )
        skill = created.json()["skill"]
        repeated = await client.post(
            "/api/admin/global-skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts("admin-original", original_text.encode()),
        )
        conflict = await client.post(
            "/api/admin/global-skills/import-directory",
            data={"on_conflict": "fail"},
            files=_directory_parts("admin-changed", changed_text.encode()),
        )
        overwritten = await client.post(
            "/api/admin/global-skills/import-directory",
            data={
                "on_conflict": "overwrite",
                "expected_hash": skill["bundle_hash"],
            },
            files=_directory_parts("admin-changed", changed_text.encode()),
        )
        renamed = await client.post(
            "/api/admin/global-skills/import-directory",
            data={"on_conflict": "rename", "target_name": "admin-conflict-copy"},
            files=_directory_parts("admin-renamed", changed_text.encode()),
        )

    assert (created.status_code, created.json()["status"]) == (201, "created")
    assert (repeated.status_code, repeated.json()["status"]) == (
        200,
        "already_imported",
    )
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (
        409,
        "skill_import_conflict",
    )
    assert (overwritten.status_code, overwritten.json()["status"]) == (
        200,
        "overwritten",
    )
    assert (renamed.status_code, renamed.json()["status"]) == (201, "renamed")


@pytest.mark.asyncio
async def test_registered_user_can_read_global_detail(settings_factory) -> None:
    async with skill_api_client(settings_factory) as client:
        created = await _import_archive(
            client,
            "/api/admin/global-skills/import",
            _skill_text("admin-detail-denied", "Admin detail denied"),
        )
        visible = await client.get(
            f"/api/admin/global-skills/{created.json()['skill']['id']}",
            headers={"X-Test-User": "member"},
        )

    assert visible.status_code == 200
    assert visible.json()["scope"] == "global"


@pytest.mark.asyncio
async def test_personal_archive_upload_requires_owner_before_reading_body(
    settings_factory,
) -> None:
    boundary = "personal-archive-auth"
    async with skill_api_client(settings_factory) as client:
        status_code, payload, body_reads = await _asgi_post_without_body_read(
            client._transport.app,
            path="/api/workspaces/team/skills/import",
            user="member",
            content_type=f"multipart/form-data; boundary={boundary}",
            body=b"body must remain unread",
        )

    assert body_reads == 0
    assert (status_code, payload["error"]["code"]) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
async def test_registered_user_can_archive_another_users_global_skill(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as client:
        created = await _import_archive(
            client,
            "/api/admin/global-skills/import",
            _skill_text("archive-global", "Archive global"),
        )
        skill = created.json()["skill"]

        archived = await client.request(
            "DELETE",
            f"/api/admin/global-skills/{skill['id']}",
            json={"expected_hash": skill["bundle_hash"]},
            headers={"X-Test-User": "member"},
        )

    assert archived.status_code == 204


@pytest.mark.asyncio
async def test_personal_detail_can_read_artifact_manifest(settings_factory) -> None:
    text = _skill_text("artifact-detail", "Artifact detail")
    async with skill_api_client(settings_factory) as client:
        created = await client.post(
            "/api/workspaces/personal/skills/import",
            files={
                "archive": (
                    "artifact.zip",
                    _archive(
                        ("SKILL.md", text.encode()),
                        ("references/policy.txt", b"policy"),
                    ),
                    "application/zip",
                )
            },
        )
        skill = created.json()["skill"]
        detail = await client.get(
            f"/api/workspaces/personal/skills/{skill['id']}"
        )

    assert detail.status_code == 200
    assert detail.json()["content"] == text
    assert detail.json()["files"] == [
        {
            "path": "references/policy.txt",
            "mime_type": "text/plain",
            "size_bytes": 6,
            "sha256": "sha256:823412d1eacb67956220e532959f0104603057c88704863ca38e7cd188fda812",
        }
    ]


@pytest.mark.asyncio
async def test_global_detail_is_visible_but_personal_mutation_rejects_wrong_scope(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as client:
        created = await _import_archive(
            client,
            "/api/admin/global-skills/import",
            _skill_text("detail-global", "Detail global"),
        )
        skill = created.json()["skill"]

        visible = await client.get(
            f"/api/workspaces/personal/skills/{skill['id']}"
        )
        wrong_scope = await client.request(
            "DELETE",
            f"/api/workspaces/personal/skills/{skill['id']}",
            json={"expected_hash": skill["bundle_hash"]},
        )
        admin_detail = await client.get(
            f"/api/admin/global-skills/{skill['id']}"
        )

    assert visible.status_code == 200
    assert visible.json()["scope"] == "global"
    assert (wrong_scope.status_code, wrong_scope.json()["error"]["code"]) == (
        404,
        "skill_not_found",
    )
    assert admin_detail.status_code == 200
    assert admin_detail.json()["scope"] == "global"


@pytest.mark.asyncio
async def test_excluded_manual_create_raw_update_and_copy_routes_fail_closed(
    settings_factory,
) -> None:
    async with skill_api_client(settings_factory) as client:
        manual_create = await client.post(
            "/api/workspaces/personal/skills", json={"content": VALID_SKILL_TEXT}
        )
        imported = await _import_archive(
            client,
            "/api/workspaces/personal/skills/import",
            VALID_SKILL_TEXT,
        )
        skill = imported.json()["skill"]
        changed = VALID_SKILL_TEXT.replace("Review changes", "Review safely")
        raw_update = await client.patch(
            f"/api/skills/{skill['id']}",
            json={
                "expected_hash": skill["bundle_hash"],
                "enabled": False,
                "content": changed,
            },
        )
        copied = await client.post(
            f"/api/skills/{skill['id']}/copy",
            json={"target_workspace_id": "legacy-team"},
        )
        catalog = await client.get("/api/workspaces/personal/skills")

    assert manual_create.status_code == 405
    assert raw_update.status_code == 422
    assert copied.status_code == 404
    assert set(catalog.json()) == {
        "global",
        "personal",
        "effective_count",
        "changes_apply_to",
    }
    assert "actions" not in catalog.json()["personal"][0]


@pytest.mark.asyncio
async def test_archive_upload_parses_fail_overwrite_and_rename_conflict_fields(
    settings_factory,
) -> None:
    original_text = _skill_text("archive-conflict", "Original")
    changed_text = _skill_text("archive-conflict", "Changed")
    renamed_text = _skill_text("archive-conflict", "Renamed")
    async with skill_api_client(settings_factory) as client:
        created = await _import_archive(
            client,
            "/api/workspaces/personal/skills/import",
            original_text,
        )
        original = created.json()["skill"]
        overwritten = await _import_archive(
            client,
            "/api/workspaces/personal/skills/import",
            changed_text,
            on_conflict="overwrite",
            expected_hash=original["bundle_hash"],
        )
        renamed = await _import_archive(
            client,
            "/api/workspaces/personal/skills/import",
            renamed_text,
            on_conflict="rename",
            target_name="archive-renamed",
        )

    assert created.status_code == 201
    assert created.json()["status"] == "created"
    assert overwritten.status_code == 200
    assert overwritten.json()["status"] == "overwritten"
    assert renamed.status_code == 201
    assert renamed.json()["status"] == "renamed"
    assert renamed.json()["skill"]["name"] == "archive-renamed"

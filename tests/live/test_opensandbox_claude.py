import asyncio
import json
import os
import shlex
import uuid
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_OPENSANDBOX_CLAUDE") != "1",
    reason="Set RUN_LIVE_OPENSANDBOX_CLAUDE=1 for the real Claude sandbox gate.",
)


@pytest.mark.asyncio
async def test_real_claude_vault_skill_resume_memory_and_fail_closed(tmp_path) -> None:
    from opensandbox import Sandbox

    from app.auth.models import IdentityContext
    from app.config import Settings
    from app.db.models import SessionSandboxRecord
    from app.sandbox.contracts import SandboxError
    from app.sandbox.main import build_execution_worker
    from app.skills.bundle import build_bundle

    required = (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "OPENSANDBOX_API_URL",
        "OPENSANDBOX_API_KEY",
        "OPENSANDBOX_RUNNER_IMAGE",
        "TEST_POSTGRES_URL",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.fail("Missing live gate configuration: " + ", ".join(missing))

    run_id = uuid.uuid4().hex
    workspace_id = f"vault-live-{run_id[:20]}"
    user_id = f"vault-user-{run_id[:20]}"
    skill_marker = f"SKILL-{run_id}"
    memory_marker = f"MEMORY-{run_id}"
    workspaces_root = tmp_path / "workspaces"
    workspace_root = workspaces_root / workspace_id
    workspace_root.mkdir(parents=True)
    (workspace_root / "workspace.yaml").write_text(
        f"""version: 1
id: {workspace_id}
name: Vault Live Gate
description: Real OpenSandbox Claude credential and persistence gate.
skills: []
allowed_tools: [Read, Write, Edit, Glob, Grep]
mcp_servers: {{}}
""",
        encoding="utf-8",
    )
    (workspace_root / "CLAUDE.md").write_text(
        "Use requested managed Skills. When explicitly asked to remember a marker, "
        "write it to Auto Memory before replying. Reply briefly.\n",
        encoding="utf-8",
    )
    endpoint_host = urlsplit(os.environ["ANTHROPIC_BASE_URL"]).hostname
    assert endpoint_host is not None
    settings = Settings(
        _env_file=None,
        anthropic_base_url=os.environ["ANTHROPIC_BASE_URL"],
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        workspaces_root=workspaces_root,
        app_data_dir=tmp_path / "data",
        database_url=os.environ["TEST_POSTGRES_URL"],
        app_runtime_mode="opensandbox_docker",
        app_runtime_cohort=f"live-{run_id[:12]}",
        opensandbox_api_url=os.environ["OPENSANDBOX_API_URL"],
        opensandbox_api_key=os.environ["OPENSANDBOX_API_KEY"],
        opensandbox_runner_image=os.environ["OPENSANDBOX_RUNNER_IMAGE"],
        opensandbox_runner_runtime="claude",
        opensandbox_allowed_hosts=(endpoint_host,),
        opensandbox_ready_timeout_seconds=120,
        opensandbox_sandbox_timeout_seconds=900,
        turn_timeout_seconds=300,
        mock_personal_workspace_id=workspace_id,
    )
    services, worker = build_execution_worker(settings)
    services.turns.execution_availability = None
    identity = IdentityContext(user_id, user_id, "Vault Live User")
    session_ids: set[str] = set()
    volume_names: set[str] = set()
    database_ready = False

    async def execute(session_id: str, text: str, request_id: str):
        turn = await services.turns.start(
            session_id, text, [], f"{request_id}-{run_id[:8]}"
        )
        assert await worker.execute_one() == turn.id
        completed = await services.turns.get(turn.id)
        assert completed.status == "completed", (
            completed.error_code,
            completed.error_message,
        )
        return completed

    try:
        services.memory_scopes.initialize()
        await services.database.initialize()
        database_ready = True
        entries = services.workspaces.scan()
        await services.workspace_sync.sync(entries, identity)
        skill = build_bundle(
            b"""---
name: vault-canary
description: Use when the user asks for the vault live gate marker.
---
Read `references/canary.txt` and return its exact contents. Do not guess.
""",
            [("references/canary.txt", skill_marker.encode())],
            services.skills.limits,
        )
        await services.skills.import_bundle(
            workspace_id,
            identity,
            skill,
            enabled=True,
            origin={"type": "live_gate"},
        )

        first_session = await services.sessions.create(workspace_id, identity)
        session_ids.add(first_session.id)
        first_turn = await execute(
            first_session.id,
            "/vault-canary Return only the exact marker from the managed Skill.",
            "skill",
        )
        first_text = await _assistant_text(services, first_turn.id)
        assert skill_marker in first_text
        first_state = await services.sessions.get(first_session.id)
        assert first_state.claude_session_id
        assert (first_turn.input_tokens or 0) > 0
        assert (first_turn.output_tokens or 0) > 0

        resume_turn = await execute(
            first_session.id,
            f"Use the Write tool to create /memory/MEMORY.md containing the exact "
            f"marker {memory_marker}. Then use the Read tool to verify that exact "
            "file. Reply only SAVED after both tool calls succeed.",
            "resume-memory-write",
        )
        resumed_state = await services.sessions.get(first_session.id)
        assert resumed_state.claude_session_id == first_state.claude_session_id
        assert (resume_turn.input_tokens or 0) > 0
        assert (resume_turn.output_tokens or 0) > 0

        async with services.database.session() as db:
            first_record = await db.get(SessionSandboxRecord, first_session.id)
        assert first_record is not None and first_record.sandbox_id
        connected = await Sandbox.connect(
            first_record.sandbox_id,
            connection_config=worker.sandbox._connection,
        )
        memory_probe = await connected.commands.run(
            "grep -R -F -- " + shlex.quote(memory_marker) + " /memory >/dev/null"
        )
        assert memory_probe.exit_code == 0

        second_session = await services.sessions.create(workspace_id, identity)
        session_ids.add(second_session.id)
        recall_turn = await execute(
            second_session.id,
            "Read Auto Memory and reply only with the previously saved MEMORY marker.",
            "memory-read",
        )
        assert memory_marker in await _assistant_text(services, recall_turn.id)
        assert (recall_turn.input_tokens or 0) > 0
        assert (recall_turn.output_tokens or 0) > 0

        async with services.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(SessionSandboxRecord).where(
                            SessionSandboxRecord.session_id.in_(
                                (first_session.id, second_session.id)
                            )
                        )
                    )
                ).all()
            )
        assert len(records) == 2
        adapter = worker.sandbox
        for record in records:
            assert record.sandbox_id
            volume_names.update((record.session_volume_name, record.memory_volume_name))
            connected = await Sandbox.connect(
                record.sandbox_id,
                connection_config=adapter._connection,
            )
            environment = await connected.commands.run(
                "printf '%s\\n' \"${ANTHROPIC_API_KEY:-}\""
            )
            assert environment.exit_code == 0
            assert environment.text.strip() == "opensandbox-vault-placeholder"
            request_text = await connected.files.read_file(
                "/session/control/request.json"
            )
            assert os.environ["ANTHROPIC_API_KEY"] not in request_text

        persisted = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in settings.app_data_dir.rglob("*")
            if path.is_file()
        )
        assert os.environ["ANTHROPIC_API_KEY"] not in persisted
        event_payloads = []
        for turn in (first_turn, resume_turn, recall_turn):
            event_payloads.extend(
                event.payload_json
                for event in await services.turns.list_events(turn.id)
            )
        event_text = "\n".join(event_payloads)
        assert os.environ["ANTHROPIC_API_KEY"] not in event_text

        second_record = next(
            record for record in records if record.session_id == second_session.id
        )
        connected = await Sandbox.connect(
            second_record.sandbox_id,
            connection_config=adapter._connection,
        )
        await connected.credential_vault.delete()
        command = await adapter.run_turn(
            _handle_for(second_record), "/session/control/request.json"
        )
        frames = await _collect_frames(adapter, command)
        observation = await adapter.inspect_command(command)
        terminal = [frame for frame in frames if frame.kind == "terminal"]
        assert observation.exit_code != 0
        assert terminal and terminal[-1].payload.get("status") == "failed"
        assert os.environ["ANTHROPIC_API_KEY"] not in "\n".join(
            frame.to_line() for frame in frames
        )
    finally:
        if database_ready and session_ids:
            async with services.database.session() as db:
                cleanup_records = list(
                    (
                        await db.scalars(
                            select(SessionSandboxRecord).where(
                                SessionSandboxRecord.session_id.in_(session_ids)
                            )
                        )
                    ).all()
                )
            for record in cleanup_records:
                volume_names.update(
                    (record.session_volume_name, record.memory_volume_name)
                )
                if record.sandbox_id:
                    try:
                        await worker.sandbox.destroy_sandbox(record.sandbox_id)
                    except SandboxError:
                        pass
        await services.turns.shutdown()
        await services.database.dispose()
        if os.environ.get("KEEP_OPENSANDBOX_TEST_VOLUMES") != "1":
            await _remove_volumes(volume_names)


async def _assistant_text(services, turn_id: str) -> str:
    events = await services.turns.list_events(turn_id)
    return "\n".join(
        str(json.loads(event.payload_json).get("text", ""))
        for event in events
        if event.event_type
        in {
            "message.assistant.delta",
            "message.assistant.completed",
        }
    )


def _handle_for(record):
    from app.sandbox.models import SandboxHandle

    return SandboxHandle(
        sandbox_id=record.sandbox_id,
        generation=record.generation,
        created_at=record.created_at,
        image_reference=record.runner_image,
        session_volume_name=record.session_volume_name,
        memory_volume_name=record.memory_volume_name,
    )


async def _collect_frames(adapter, command):
    from app.runner.protocol import RunnerFrame

    cursor = None
    frames: list[RunnerFrame] = []
    for _ in range(1_200):
        batch = await adapter.read_frames(command, cursor)
        cursor = batch.next_cursor
        frames.extend(RunnerFrame.model_validate_json(line) for line in batch.lines)
        if batch.complete:
            return frames
        await asyncio.sleep(0.25)
    pytest.fail("Timed out waiting for the no-fallback command")


async def _remove_volumes(volume_names: set[str]) -> None:
    for volume in volume_names:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "volume",
            "rm",
            volume,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()

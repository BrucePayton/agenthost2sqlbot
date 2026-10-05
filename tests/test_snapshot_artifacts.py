from datetime import UTC, datetime, timedelta

import pytest


def test_snapshot_artifact_store_binds_random_refs_to_page_and_ttl() -> None:
    from app.agui.snapshot_artifacts import (
        SnapshotArtifactNotFound,
        SnapshotArtifactStore,
        read_widget_data,
    )

    now = datetime(2026, 8, 8, tzinfo=UTC)
    store = SnapshotArtifactStore(
        ttl_seconds=30, max_items=2, max_total_bytes=4096, now=lambda: now
    )
    first = store.create(
        owner_key="actor-1",
        page_instance_id="workbench-1",
        resource_id="dashboard-88",
        artifact={
            "widgets": [
                {"widgetId": "sales", "fields": ["amount"], "rows": [{"amount": 42}]}
            ]
        },
    )
    second = store.create(
        owner_key="actor-1",
        page_instance_id="workbench-1",
        resource_id="dashboard-88",
        artifact={"widgets": []},
    )

    assert first != second
    assert (
        store.read(
            first,
            owner_key="actor-1",
            page_instance_id="workbench-1",
            resource_id="dashboard-88",
        )["widgets"][0]["widgetId"]
        == "sales"
    )
    with pytest.raises(SnapshotArtifactNotFound):
        store.read(
            first,
            owner_key="actor-2",
            page_instance_id="workbench-1",
            resource_id="dashboard-88",
        )
    with pytest.raises(SnapshotArtifactNotFound):
        store.read(
            first,
            owner_key="actor-1",
            page_instance_id="workbench-1",
            resource_id="dashboard-99",
        )

    result = read_widget_data(
        store,
        owner_key="actor-1",
        page_instance_id="workbench-1",
        resource_id="dashboard-88",
        arguments={
            "snapshotRef": first,
            "widgetId": "sales",
            "offset": 0,
            "limit": 1,
            "fields": ["amount"],
        },
    )
    assert result == {
        "summary": '组件 sales：返回 1 行，字段 amount',
        "widgetId": "sales",
        "title": "sales",
        "fields": ["amount"],
        "rows": [[42]],
        "offset": 0,
        "returnedRows": 1,
    }

    now += timedelta(seconds=31)
    with pytest.raises(SnapshotArtifactNotFound):
        store.read(
            first,
            owner_key="actor-1",
            page_instance_id="workbench-1",
            resource_id="dashboard-88",
        )


def test_snapshot_store_evicts_oldest_items_to_stay_within_global_quota() -> None:
    from app.agui.snapshot_artifacts import (
        SnapshotArtifactNotFound,
        SnapshotArtifactStore,
    )

    store = SnapshotArtifactStore(max_items=2, max_total_bytes=4096)
    refs = [
        store.create(
            owner_key="actor-1",
            page_instance_id="page-1",
            resource_id="dashboard-88",
            artifact={"widgets": [], "sequence": index},
        )
        for index in range(3)
    ]

    with pytest.raises(SnapshotArtifactNotFound):
        store.read(
            refs[0],
            owner_key="actor-1",
            page_instance_id="page-1",
            resource_id="dashboard-88",
        )
    assert store.item_count == 2


@pytest.mark.asyncio
async def test_snapshot_upload_creates_only_server_owned_reference(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    settings = settings_factory(davinci_local_integration=True)
    write_workspace(settings.workspaces_root, "actual")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/agent-api/artifacts/snapshots",
            json={
                "pageInstanceId": "workbench-1",
                "artifact": {
                    "schemaVersion": "davinci-dashboard-snapshot-v1",
                    "pageInstanceId": "workbench-1",
                    "dashboardId": "dashboard-88",
                    "contextVersion": 3,
                    "filters": [],
                    "widgets": [],
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["snapshotRef"].startswith("snap_")
    stored = app.state.snapshot_artifacts.read(
        response.json()["snapshotRef"],
        owner_key="u-"
        + __import__("hashlib")
        .sha256(settings.mock_user_id.encode("utf-8"))
        .hexdigest()[:32],
        page_instance_id="workbench-1",
        resource_id="dashboard-88",
    )
    assert stored["contextVersion"] == 3


@pytest.mark.asyncio
async def test_snapshot_upload_fails_closed_outside_local_integration(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    settings = settings_factory(davinci_local_integration=False)
    write_workspace(settings.workspaces_root, "actual")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/agent-api/artifacts/snapshots",
            json={"pageInstanceId": "page-1", "artifact": {}},
        )

    assert response.status_code == 503


def test_core_mcp_exposes_only_snapshot_widget_reader() -> None:
    from app.agui.claude_tools import build_davinci_core_mcp_server
    from app.agui.snapshot_artifacts import SnapshotArtifactStore

    server = build_davinci_core_mcp_server(
        SnapshotArtifactStore(),
        owner_key="actor-1",
        page_instance_id="workbench-1",
        resource_id="dashboard-88",
    )

    assert server["type"] == "sdk"
    assert server["name"] == "davinci_core"

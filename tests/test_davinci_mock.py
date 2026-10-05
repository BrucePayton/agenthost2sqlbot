import httpx
import pytest


@pytest.mark.asyncio
async def test_mock_routes_share_shell_and_pin_agent_origin() -> None:
    from demo.davinci_mock.app import create_mock_app

    app = create_mock_app("http://127.0.0.1:8999")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mock"
    ) as client:
        dashboard = await client.get("/dashboard/1024")
        datasets = await client.get("/datasets")
        missing = await client.get("/dashboard/9999")
        health = await client.get("/health")

    assert dashboard.status_code == datasets.status_code == 200
    assert 'data-agent-origin="http://127.0.0.1:8999"' in dashboard.text
    assert 'id="agentFrame"' in dashboard.text
    assert 'id="davinciContent"' in dashboard.text
    assert missing.status_code == 404
    assert health.json() == {"status": "ready"}


def test_mock_rejects_non_loopback_agent_origin() -> None:
    from demo.davinci_mock.app import create_mock_app

    with pytest.raises(ValueError, match="loopback"):
        create_mock_app("https://agent.example.com")

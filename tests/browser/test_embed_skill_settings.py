"""Exercise Skill settings races through the built iframe's real UI."""
# Imported pytest fixtures are injected through same-named test arguments.
# ruff: noqa: F811
import asyncio

import pytest
from playwright.async_api import expect

from tests.browser.test_embed_reliability import embed_page  # noqa: F401


@pytest.mark.asyncio
async def test_new_session_waits_for_closing_skill_save(embed_page):
    """Returning to chat cannot let New Session overtake a pending disable."""
    page, frame, _state = embed_page
    saving = asyncio.Event()
    release_save = asyncio.Event()
    created = asyncio.Event()
    enabled = True
    created_with_enabled = []

    async def workspace_route(route):
        """Expose the personal workspace used by both chat and settings."""
        await route.fulfill(json=[{
            "id": "actual", "kind": "personal", "available": True,
            "can_manage_skills": True, "can_manage_global_skills": False,
            "skill_count": 1,
        }])

    async def skills_route(route):
        """Return the actual saved state to the manager's refresh."""
        await route.fulfill(json={
            "global": [{"id": "discovery", "name": "data-discovery",
                        "scope": "global", "enabled": enabled,
                        "version_no": 1, "description": "Find data"}],
            "personal": [], "effective_count": int(enabled),
        })

    async def toggle_route(route):
        """Hold the write response so the user can return and create a session."""
        nonlocal enabled
        saving.set()
        await release_save.wait()
        enabled = False
        await route.fulfill(json={"enabled": False})

    async def sessions_route(route):
        """Capture which saved state the creation request would snapshot."""
        if route.request.method != "POST":
            await route.fallback()
            return
        created_with_enabled.append(enabled)
        created.set()
        await route.fulfill(json={"id": "session-after-save"})

    await page.route("**/api/workspaces", workspace_route)
    await page.route("**/api/workspaces/actual/skills", skills_route)
    await page.route("**/api/workspaces/actual/global-skills/discovery/setting", toggle_route)
    await page.reload()
    await expect(frame.locator("#agentSkillsButton")).to_be_visible()
    await frame.locator("#agentSkillsButton").click()
    await frame.locator("#globalSkillList").get_by_role("button", name="关闭", exact=True).click()
    await asyncio.wait_for(saving.wait(), timeout=3)
    await frame.locator("#skillManagementBackButton").click()
    await page.route("**/api/workspaces/actual/sessions", sessions_route)
    try:
        await frame.locator("#agentNewSessionButton").click()
        await expect(frame.locator("#agentNewSessionButton")).to_be_disabled()
        await expect(frame.locator("#agentSendButton")).to_be_disabled()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(created.wait(), timeout=0.2)
    finally:
        release_save.set()
    await expect(frame.locator("#agentSessionId")).to_have_text("session-after-save")
    assert created_with_enabled == [False]


@pytest.mark.asyncio
async def test_changed_skill_rejection_preserves_input_without_unknown_run(embed_page):
    """A stale blank session leaves a usable draft and no phantom accepted turn."""
    page, frame, state = embed_page

    async def reject_stale_session(route):
        """Return the pre-run error emitted by the real Host guard."""
        await route.fulfill(status=409, json={"error": {
            "code": "session_skills_changed",
            "message": "Skill 配置已变化，请新建会话后重新发送；本次消息尚未执行。",
        }})

    await page.route("**/api/ag-ui", reject_stale_session)
    await frame.locator("#agentMessageInput").fill("查找数据")
    await frame.locator("#agentSendButton").click()
    await expect(frame.locator("#agentMessageInput")).to_have_value("查找数据")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    await expect(frame.locator(".agent-message.user")).to_have_count(0)
    await expect(frame.locator("#agentCheckRun")).to_be_hidden()
    assert state["acceptedRuns"] == []

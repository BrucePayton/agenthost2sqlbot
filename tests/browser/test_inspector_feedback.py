"""Exercise the real Inspector page and API with an isolated fake-runtime database."""

from urllib.parse import urlsplit

import pytest
from playwright.async_api import async_playwright, expect

from tests.test_inspector_feedback_stats import AUTH, rate_task
from tests.test_session_inspector import inspector_client


@pytest.mark.asyncio
async def test_feedback_filters_totals_plaintext_and_original_reply(settings_factory):
    """Statistics and task evidence remain consistent across filters and view changes."""
    async with inspector_client(settings_factory) as (client, _app):
        first = await rate_task(client, "feedback-user-one", "up")
        await rate_task(client, "feedback-user-two", "down")
        await rate_task(client, "feedback-user-one", "up")
        comment = '<img src=x onerror="window.commentExecuted=true">原样备注'
        await client.put(
            first[3],
            headers=first[4],
            json={"rating": "up", "reasons": ["accurate", "clear"], "comment": comment},
        )
        state = {"fail": False, "boundedReads": 0}
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def route(request_route):
                """Route browser requests to the actual ASGI app, never the network."""
                parsed = urlsplit(request_route.request.url)
                assert parsed.netloc == "inspector.test"
                if state["fail"] and parsed.path.endswith("feedbackStats"):
                    await request_route.fulfill(
                        status=500, json={"error": {"message": "测试读取失败"}}
                    )
                    return
                if parsed.path.endswith("/events") and not parsed.query:
                    await request_route.fulfill(
                        status=413, json={"error": {"message": "use a bounded turn"}}
                    )
                    return
                if parsed.path.endswith("/events") and parsed.query:
                    state["boundedReads"] += 1
                response = await client.get(
                    parsed.path + ("?" + parsed.query if parsed.query else ""),
                    auth=AUTH,
                )
                await request_route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    content_type=response.headers.get(
                        "content-type", "application/json"
                    ),
                )

            await page.route("**/*", route)
            await page.goto("http://inspector.test/inspector")
            await page.get_by_role("button", name="反馈统计", exact=True).click()
            await expect(page.locator("#feedbackSummary strong")).to_have_text(
                ["3", "2", "1", "66.7%", "2"]
            )
            await expect(page.locator("#feedbackDetails tbody tr")).to_have_count(3)
            await expect(page.locator("#feedbackDetails")).to_contain_text(comment)
            assert await page.locator("#feedbackDetails img").count() == 0
            assert await page.evaluate("window.commentExecuted === undefined")
            await page.locator("#feedbackGroupBy").select_option("actor")
            await page.get_by_role("button", name="查询", exact=True).click()
            await expect(page.locator("#feedbackGroups tbody tr")).to_have_count(2)
            await page.locator("#feedbackRating").select_option("down")
            await expect(page.locator("#feedbackDetails tbody tr")).to_have_count(1)
            await expect(page.locator("#feedbackSummary strong")).to_have_text(
                ["3", "2", "1", "66.7%", "2"]
            )
            await page.locator("#feedbackRating").select_option("")
            await expect(page.locator("#feedbackDetails tbody tr")).to_have_count(3)
            target = page.locator("#feedbackDetails tbody tr").filter(
                has_text="原样备注"
            )
            await target.get_by_role("button").click()
            await expect(page.locator("#inspectorSessionId")).to_have_text(first[0])
            await expect(page.locator(".event-card.feedback-target")).to_have_attribute(
                "data-event-id", first[1]
            )
            await expect(page.locator("#inspectorTaskFeedback")).to_contain_text(
                "原样备注"
            )
            assert state["boundedReads"] == 1
            await page.get_by_role("button", name="反馈统计", exact=True).click()
            state["fail"] = True
            await page.get_by_role("button", name="查询", exact=True).click()
            await expect(page.locator("#feedbackStatus")).to_contain_text("读取失败")
            await expect(page.locator("#feedbackSummary strong")).to_have_count(0)
            state["fail"] = False
            await page.get_by_role("button", name="清空筛选", exact=True).click()
            await expect(page.locator("#feedbackSummary strong")).to_have_count(5)
            await page.locator("#feedbackActorQuery").fill("不存在的用户")
            await page.get_by_role("button", name="查询", exact=True).click()
            await expect(page.locator("#feedbackSummary strong")).to_have_text(
                ["0", "0", "0", "—", "0"]
            )
            assert not errors, errors
            await browser.close()

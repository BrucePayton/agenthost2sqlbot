"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {buildSync} = require("esbuild");
const {spawnSync} = require("node:child_process");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");

test("real embed receipt shows business summary; index never opens technical details", () => {
  // Reuse the dual-origin fixture, but serve current sources from memory, not embed.js.
  const bundle = buildSync({entryPoints: [path.join(root, "web/embed/main.js")], bundle: true,
    format: "esm", platform: "browser", write: false}).outputFiles[0].text;
  const script = String.raw`
import asyncio, importlib.util, json, sys
from pathlib import Path
from playwright.async_api import expect
spec = importlib.util.spec_from_file_location("receipt_harness", "tests/browser/test_embed_reliability.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
bundle = json.load(sys.stdin)["bundle"]
async def main():
    fixture = h.embed_page.__wrapped__()
    page, frame, state = await anext(fixture)
    try:
        await page.route("**/static/embed.js*", lambda route: route.fulfill(body=bundle, content_type="text/javascript"))
        receipt = {"status":"error", "error":{"code":"INVALID_ARGUMENT", "message":"raw-secret"},
            "issues":[{"code":"LAYOUT_NO_READABLE_CANDIDATES", "message":"raw-secret", "widgetIds":["card-7"],
                "constraints":{"chartType":11001,"widgetTitle":"区域营收"}, "retryable":False}]}
        state["history"] = [
            {"id":"s", "turn_id":"R", "event_type":"turn.started", "payload":{}, "created_at":"2026-09-13T00:00:00Z"},
            {"id":"ts", "turn_id":"R", "event_type":"tool.started", "payload":{"tool_use_id":"receipt", "name":"dashboard.set_widget_layout", "input_preview":"{}"}, "created_at":"2026-09-13T00:00:01Z"},
            {"id":"te", "turn_id":"R", "event_type":"tool.completed", "payload":{"tool_use_id":"receipt", "is_error":True, "output_preview":json.dumps(receipt,ensure_ascii=False)}, "created_at":"2026-09-13T00:00:02Z"},
            {"id":"e", "turn_id":"R", "event_type":"turn.completed", "payload":{}, "created_at":"2026-09-13T00:00:03Z"}]
        for width in (390, 1000):
            await page.set_viewport_size({"width":width,"height":1000})
            await page.reload()
            await page.locator("iframe").evaluate("(el,width) => el.style.width = Math.min(width-20,600)+'px'", width)
            await frame.get_by_role("button", name="查看工具和 Skill 调用").click()
            await frame.locator(".ta-call-popup button").first.click()
            row = frame.locator('.ti[data-tool-use-id="receipt"]')
            await expect(row).to_be_visible()
            text = await row.inner_text()
            assert "调整看板布局" in text, text
            assert "排行榜" in text and "区域营收" in text, text
            assert "核对" in text and "保存" in text, text
            assert not any(x in text for x in ("INVALID_ARGUMENT", "dashboard.set_widget_layout", "raw-secret", "card-7", "编号")), text
            await expect(row.locator(".ti-io")).to_be_hidden()
            assert await row.evaluate("el => el.scrollWidth <= el.clientWidth"), "receipt overflow"
            await row.get_by_role("button",name="技术详情",exact=True).click()
            await expect(row.locator(".ti-io")).to_be_visible()
            await expect(row.locator(".ti-io")).to_contain_text("INVALID_ARGUMENT")
            await expect(row.locator(".ti-io")).to_contain_text("dashboard.set_widget_layout")
            await row.get_by_role("button",name="技术详情",exact=True).click()
            await page.screenshot(path=f"/private/tmp/host-business-receipt-{width}.png")
        assert await page.evaluate("window.businessWrites") == 0
        assert state["runs"] == []
        followup = '布局结果以上方业务摘要为准。后续模型会话未能完成，不要重复提交布局操作；如需排查，请联系管理员并提供操作时间。'
        state['history'][-1]['event_type'] = 'turn.failed'
        state['history'][-1]['payload'] = {'code':'claude_rate_limited', 'message':followup}
        await page.reload()
        await frame.locator('.ta-bar').click()
        await expect(frame.locator('.ti.error .name')).to_have_text(followup)
        assert '稍后再试' not in await frame.locator('.ti.error').inner_text()
        workbench = await page.context.browser.new_page()
        workbench_errors, writes = [], []
        workbench.on("pageerror", lambda error: workbench_errors.append(str(error)))
        async def workbench_route(route):
            path = h.urlparse(route.request.url).path
            if route.request.method != 'GET':
                writes.append(path)
                await route.abort()
            elif path.startswith('/static/'):
                await route.fulfill(path=h.ROOT / 'app/web' / path.lstrip('/'))
            elif path == '/':
                template = h.Environment(loader=h.FileSystemLoader(h.ROOT / 'app/web/templates')).get_template('index.html')
                await route.fulfill(content_type='text/html', body=template.render(app_name='Test',max_files_per_turn=5,debug_identity_enabled=False))
            elif path == '/api/health':
                await route.fulfill(json={'status':'ready'})
            else:
                await route.fulfill(json=[])
        await workbench.route('**/*', workbench_route)
        await workbench.goto('http://workbench.test/')
        await workbench.wait_for_function("typeof userFacingUI !== 'undefined'")
        await workbench.evaluate('''receipt => {
            renderFrontendToolDeferred({tool_use_id:'wb',name:'dashboard.set_widget_layout',arguments:{token:'private-input'}},{});
            applyToolResults({tool_results:[{tool_call_id:'wb',is_error:true,content:JSON.stringify({...receipt,token:'private-output'})}]},{});
            renderToolStarted({tool_use_id:'live',name:'dashboard.set_widget_layout',input_preview:'{}'});
            renderToolCompleted({tool_use_id:'live',is_error:true,output_preview:JSON.stringify(receipt)});
        }''', receipt)
        await workbench.evaluate('message => appendSystemEvent({code:"claude_rate_limited",message},true)', followup)
        assert '稍后再试' not in await workbench.locator('.system-event.error').inner_text()
        await expect(workbench.locator('.system-event.error')).to_contain_text(followup)
        for width in (390, 1000):
            await workbench.set_viewport_size({'width':width,'height':1000})
            for selector in ('.timeline-tool', '.tool-event'):
                row = workbench.locator(selector)
                text = await row.inner_text()
                assert '调整看板布局' in text and '区域营收' in text, text
                assert not any(raw in text for raw in ('INVALID_ARGUMENT','raw-secret','dashboard.set_widget_layout')), text
                assert await row.evaluate('el => el.scrollWidth <= el.clientWidth'), 'workbench overflow'
                await row.get_by_text('技术详情',exact=True).click()
                await expect(row).to_contain_text('INVALID_ARGUMENT')
                assert 'private-input' not in await row.inner_text()
                assert 'private-output' not in await row.inner_text()
                await row.get_by_text('技术详情',exact=True).click()
            await workbench.screenshot(path=f'/private/tmp/host-workbench-receipt-{width}.png')
        assert not workbench_errors, workbench_errors
        assert not writes, writes
        await workbench.close()
    finally:
        await fixture.aclose()
    for check, args in (
        (h.test_tool_summary_lists_calls_and_jumps_to_skill_output, ()),
        (h.test_business_errors_hide_raw_details_and_fit_panel, (Path('/private/tmp'), 390)),
        (h.test_business_errors_hide_raw_details_and_fit_panel, (Path('/private/tmp'), 1000)),
        (h.test_runtime_and_activity_errors_share_business_copy, ('claude_auth_failed', '服务配置', '系统管理员')),
        (h.test_runtime_and_activity_errors_share_business_copy, ('claude_rate_limited', '繁忙', '技术支持')),
    ):
        fixture = h.embed_page.__wrapped__()
        case = await anext(fixture)
        try:
            await case[0].route("**/static/embed.js*", lambda route: route.fulfill(body=bundle, content_type="text/javascript"))
            await case[0].reload()
            await check(case, *args)
        finally:
            await fixture.aclose()
asyncio.run(main())
`;
  const result = spawnSync(process.env.HOST_TEST_PYTHON || path.join(root, ".venv/bin/python"), ["-c", script], {
    cwd: root, input: JSON.stringify({bundle}), encoding: "utf8", maxBuffer: 8 * 1024 * 1024,
    env: {...process.env, PLAYWRIGHT_BROWSERS_PATH: process.env.PLAYWRIGHT_BROWSERS_PATH || "/private/tmp/davinci-playwright"},
    timeout: 90000,
  });
  assert.equal(result.status, 0, result.stderr || result.stdout || String(result.error));
});

"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

async function loadRenderer() {
  return import("../../web/embed/assistant-markdown.mjs");
}

test("assistant Markdown renders headings, emphasis, lists, and tables", async () => {
  const {renderAssistantMarkdown} = await loadRenderer();
  const html = renderAssistantMarkdown(`### 配色方案

**重点**

- 第一项
- 第二项

| 组件 | 底色 |
| --- | --- |
| 趋势图 | 蓝色 |`);

  assert.match(html, /<h3>配色方案<\/h3>/u);
  assert.match(html, /<strong>重点<\/strong>/u);
  assert.match(html, /<ul>[\s\S]*<li>第一项<\/li>/u);
  assert.match(html, /<table>[\s\S]*<th>组件<\/th>[\s\S]*<td>蓝色<\/td>/u);
});

test("assistant Markdown escapes raw HTML and unsafe links", async () => {
  const {renderAssistantMarkdown} = await loadRenderer();
  const html = renderAssistantMarkdown(
    '<img src=x onerror="alert(1)"> [危险链接](javascript:alert(1))'
  );

  assert.doesNotMatch(html, /<img/u);
  assert.doesNotMatch(html, /href="javascript:/u);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/u);
});

test("only assistant messages are assigned rendered HTML", async () => {
  const {renderMessageContent} = await loadRenderer();
  const assistant = {innerHTML: "", textContent: ""};
  const user = {innerHTML: "", textContent: ""};

  renderMessageContent(assistant, "assistant", "**加粗**");
  renderMessageContent(user, "user", "**不要加粗**");

  assert.match(assistant.innerHTML, /<strong>加粗<\/strong>/u);
  assert.equal(user.innerHTML, "");
  assert.equal(user.textContent, "**不要加粗**");
});

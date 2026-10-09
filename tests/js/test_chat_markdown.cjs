const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {atob};
vm.runInNewContext(fs.readFileSync('app/web/static/chat-markdown.js', 'utf8'), context);
const renderer = context.AssistantMarkdown;

test('stream chunks retain Markdown source and completion replaces the draft', () => {
  const body = {};
  renderer.render(body, 'assistant', '');
  for (const part of ['## 回答\n\n**重', '点**\n\n| 项目 | 数量 |\n', '| --- | --- |\n| X-RAY | 5 |']) renderer.append(body, part);
  assert.match(body.innerHTML, /<h2>回答<\/h2>/);
  assert.match(body.innerHTML, /<strong>重点<\/strong>/);
  assert.match(body.innerHTML, /<td>X-RAY<\/td>/);
  renderer.render(body, 'assistant', '完成\n\n```sql\nSELECT 1 < 2;\n```');
  assert.doesNotMatch(body.innerHTML, /X-RAY/);
  assert.match(body.innerHTML, /<pre><code class="language-sql">SELECT 1 &lt; 2;/);
});

test('history and user content preserve the existing HTML safety boundary', () => {
  const body = {};
  renderer.render(body, 'assistant', '<script>alert(1)</script> [x](javascript:alert(1))\n\n- **正常**');
  assert.doesNotMatch(body.innerHTML, /<script|href="javascript:/);
  assert.match(body.innerHTML, /<li><strong>正常<\/strong><\/li>/);
  const user = {};
  renderer.render(user, 'user', '**原文** <script>');
  assert.equal(user.textContent, '**原文** <script>');
  assert.equal(user.innerHTML, undefined);
});

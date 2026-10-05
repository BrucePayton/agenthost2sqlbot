const assert = require('node:assert/strict');
const test = require('node:test');
const { pathToFileURL } = require('node:url');
const moduleUrl = pathToFileURL(`${process.cwd()}/web/embed/subscription-progress.js`).href;

test('history shares live message identity with backwards-compatible fallback', async () => {
  const { subscriptionMessageId } = await import(moduleUrl);
  assert.equal(subscriptionMessageId({ id: 'db-event', payload: { noticeId: 'notice' } }), 'notice');
  assert.equal(subscriptionMessageId({ id: 'legacy-event', payload: { text: '旧进度' } }), 'legacy-event');
});

test('only current live starts with a native task and revision navigate once', async () => {
  const { createSubscriptionStageForwarder } = await import(moduleUrl);
  const calls = [];
  const forward = createSubscriptionStageForwarder(notice => { calls.push(notice); return true; });
  const notice = { noticeId: 'n', phase: 'started', runId: 'active', taskId: 'task', revision: 3, step: 'datasets' };
  assert.equal(forward(notice, 'other'), false);
  assert.equal(forward({ ...notice, phase: 'completed' }, 'active'), false);
  assert.equal(forward({ ...notice, revision: undefined }, 'active'), false);
  assert.equal(forward({ ...notice, taskId: undefined }, 'active'), false);
  assert.equal(forward(notice, 'active'), true);
  assert.equal(forward(notice, 'active'), false);
  assert.deepEqual(calls, [notice]);
  assert.equal(forward({ ...notice, noticeId: 'n2' }, null), false);
});

test('malformed stages and older revisions never reach the navigation bridge', async () => {
  const { createSubscriptionStageForwarder } = await import(moduleUrl);
  const calls = [];
  const forward = createSubscriptionStageForwarder(notice => { calls.push(notice); return true; });
  const notice = { noticeId: 'current', phase: 'started', runId: 'run', taskId: 'task', revision: 7, step: 'datasets' };
  for (const invalid of [{ revision: 0 }, { revision: true }, { revision: Number.MAX_SAFE_INTEGER + 1 },
    { taskId: ' ' }, { taskId: 'x'.repeat(257) }, { step: '/admin' }, { phase: 'failed' }, { phase: 'waiting' }]) {
    assert.equal(forward({ ...notice, ...invalid }, 'run'), false);
  }
  assert.equal(forward(notice, 'run'), true);
  assert.equal(forward({ ...notice, noticeId: 'older', revision: 3 }, 'run'), false);
  assert.deepEqual(calls, [notice]);
});

test('a bridge that is not ready does not consume a live notice', async () => {
  const { createSubscriptionStageForwarder } = await import(moduleUrl);
  let ready = false;
  const forward = createSubscriptionStageForwarder(() => ready);
  const notice = { noticeId: 'n', phase: 'started', runId: 'run', taskId: 'task', revision: 1, step: 'trigger' };
  assert.equal(forward(notice, 'run'), false);
  ready = true;
  assert.equal(forward(notice, 'run'), true);
  assert.equal(forward(notice, 'run'), false);
});

const REASONS = {
  up: { accurate: '结果准确', understood: '理解了我的需求', completed: '成功完成操作', efficient: '处理高效', clear: '展示清晰、容易理解' },
  down: { inaccurate: '结果不准确', misunderstood: '没理解我的需求', incomplete: '操作失败或未完成', slow: '处理太慢或卡住', poorPresentation: '展示效果不符合预期', tooManySteps: '反复确认或步骤太多' },
};

/** One anchor per finished question; tool continuations stay in the same trace chain. */
export function completedQuestionFeedbackTargets(events, turns) {
  const ids = new Map();
  for (const turn of turns) {
    if (!['completed', 'failed', 'cancelled'].includes(turn.status)) continue;
    if (turn.status === 'completed' && turn.items.some(item => item.kind === 'tool' && item.state === 'deferred')) continue;
    const reply = [...events].reverse().find(event =>
      event.turn_id === turn.lastTurnId && event.event_type === 'message.assistant.completed' && event.payload?.text?.trim());
    if (reply) ids.set(reply.id, turn.turnId);
  }
  return ids;
}

/** Keep unsent detail drafts in this page while votes are persisted independently. */
export function createMessageFeedback({ save, documentRef = document }) {
  let records = new Map();

  /** Replace saved history when switching sessions, dropping the old page's drafts. */
  function reset(values = []) {
    records = new Map(values.map(value => [value.taskId || value.messageId, { saved: value }]));
  }

  /** Update persisted votes without dropping unsent details on another reply. */
  function hydrate(values) {
    for (const value of values) {
      const taskId = value.taskId || value.messageId;
      if (!records.has(taskId)) records.set(taskId, { saved: value });
    }
  }

  /** Keep one task's vote and unsent drafts while its latest reply changes. */
  function element(messageId, taskId = messageId) {
    const record = records.get(taskId) || {};
    records.set(taskId, record);
    record.messageId = messageId;
    if (record.node) {
      record.node.dataset.messageId = messageId;
      return record.node;
    }
    record.rating = record.saved?.rating || null;
    record.drafts = { up: { reasons: [], comment: '' }, down: { reasons: [], comment: '' } };
    if (record.rating) record.drafts[record.rating] = {
      reasons: [...record.saved.reasons], comment: record.saved.comment,
    };
    const root = documentRef.createElement('section');
    root.className = 'message-feedback';
    root.setAttribute('aria-label', '评价本次问题的完整回复');
    root.dataset.taskId = taskId;
    root.dataset.messageId = messageId;
    record.node = root;

    /** Create text-only controls so free text never enters an HTML interpolation. */
    function button(text, action) {
      const node = documentRef.createElement('button');
      node.type = 'button';
      node.textContent = text;
      node.addEventListener('click', action);
      return node;
    }

    /** Save a whole vote; a failed request keeps the selection and an explicit retry. */
    async function persist(payload, supplement = false) {
      if (record.busy) return;
      record.busy = true;
      record.rating = payload.rating;
      record.error = '';
      record.submittingDetails = supplement;
      record.retry = null;
      render();
      try {
        record.saved = await save(record.messageId, payload);
        record.notice = supplement ? '补充已提交，感谢反馈' : payload.rating ? '评价已记录' : '评价已取消';
        if (supplement) record.open = false;
      } catch (error) {
        record.error = supplement ? '补充未提交，请重试' : '评价未保存，可重试';
        record.notice = '';
        record.retry = () => persist(payload, supplement);
      } finally {
        record.busy = false;
        render();
      }
    }

    /** Render saved state separately from optional unsent reasons and text. */
    function render() {
      root.replaceChildren();
      for (const [rating, label] of [['up', '👍 赞'], ['down', '👎 踩']]) {
        const vote = button(label, () => {
          const previous = record.rating;
          const next = previous === rating ? null : rating;
          if (previous) {
            const draft = record.drafts[previous];
            record.drafts[previous] = {
              reasons: [],
              comment: record.saved?.rating === previous && draft.comment === record.saved.comment
                ? '' : draft.comment,
            };
          }
          record.open = Boolean(next);
          // A direction switch saves no draft from the previous direction.
          persist({ rating: next, reasons: [], comment: '' });
        });
        vote.dataset.rating = rating;
        vote.setAttribute('aria-pressed', String(record.rating === rating));
        vote.disabled = record.busy;
        root.append(vote);
      }
      const status = documentRef.createElement('span');
      status.setAttribute('role', 'status');
      status.textContent = record.busy ? (record.submittingDetails ? '正在提交补充…' : '保存中…') : record.error || record.notice || '';
      root.append(status);
      if (record.retry) root.append(button(record.submittingDetails ? '重试提交补充' : '重试保存评价', record.retry));
      if (record.rating && !record.open) root.append(button('补充原因', () => { record.open = true; render(); }));
      if (!record.open || !record.rating) return;
      const details = documentRef.createElement('div');
      details.className = 'feedback-details';
      const hint = documentRef.createElement('p');
      hint.textContent = '原因可多选，也可以只写补充说明（选填）';
      details.append(hint);
      const draft = record.drafts[record.rating];
      for (const [code, label] of Object.entries(REASONS[record.rating])) {
        const wrapper = documentRef.createElement('label');
        const input = documentRef.createElement('input');
        input.type = 'checkbox';
        input.value = code;
        input.checked = draft.reasons.includes(code);
        input.disabled = record.busy;
        input.addEventListener('change', () => {
          draft.reasons = input.checked ? [...draft.reasons, code] : draft.reasons.filter(item => item !== code);
        });
        wrapper.append(input, documentRef.createTextNode(label));
        details.append(wrapper);
      }
      const comment = documentRef.createElement('textarea');
      comment.setAttribute('aria-label', '补充说明');
      comment.placeholder = '补充说明，最多 1000 个字符';
      comment.value = draft.comment;
      comment.disabled = record.busy;
      comment.addEventListener('input', () => { draft.comment = comment.value; });
      details.append(comment);
      const submit = button(record.busy && record.submittingDetails ? '提交中…' : '提交补充', () => {
        if ([...draft.comment].length > 1000) {
          record.error = '补充说明最多 1000 个字符';
          render();
          return;
        }
        persist({ rating: record.rating, reasons: [...draft.reasons], comment: draft.comment }, true);
      });
      submit.disabled = record.busy;
      details.append(submit, button('关闭', () => { record.open = false; render(); }));
      root.append(details);
    }
    render();
    return root;
  }
  return { reset, hydrate, element };
}

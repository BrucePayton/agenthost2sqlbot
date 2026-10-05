import {businessToolLabel} from "./user-facing-error.js";

/** Technical detail only: name a real Skill invocation from its recorded input. */
export function callLabel(item) {
  if (item.name !== 'Skill') return item.name;
  try {
    const input = JSON.parse(item.inputPreview);
    if (typeof input.skill === 'string') return `Skill · ${input.skill}`;
  } catch { /* Truncated previews still have a truthful generic label. */ }
  return 'Skill';
}

/** A keyboard-accessible call index in the top layer, above the scrolling timeline. */
export function createCallIndex(onSelect) {
  const root = document.createElement('div');
  root.className = 'ta-call-index';
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'ta-call-trigger';
  trigger.setAttribute('aria-label', '查看工具和 Skill 调用');
  trigger.setAttribute('aria-haspopup', 'dialog');
  trigger.setAttribute('aria-expanded', 'false');
  const popup = document.createElement('div');
  popup.className = 'ta-call-popup';
  popup.setAttribute('popover', 'auto');
  popup.setAttribute('role', 'dialog');
  popup.setAttribute('aria-label', '工具和 Skill 调用');
  let calls = [];
  let signature = '';
  popup.addEventListener('toggle', event => {
    trigger.setAttribute('aria-expanded', String(event.newState === 'open'));
  });
  /** Keep the open index current as more calls arrive, without stealing focus. */
  function renderCalls() {
    const focusedId = popup.contains(document.activeElement) ? document.activeElement.dataset.toolUseId : null;
    popup.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = '工具和 Skill 调用 · 点击查看摘要';
    popup.append(heading);
    for (const [index, item] of calls.entries()) {
      const link = document.createElement('button');
      link.type = 'button';
      const status = { running: '执行中', done: '完成', error: '报错', deferred: '等待前端', 'no-receipt': '未回执' }[item.state] || '';
      link.textContent = `${businessToolLabel(item.name)} · ${status}`;
      link.dataset.callIndex = String(index + 1);
      link.dataset.toolUseId = item.toolUseId;
      link.addEventListener('click', () => {
        popup.hidePopover();
        onSelect(item);
      });
      popup.append(link);
    }
    if (focusedId) popup.querySelector(`[data-tool-use-id="${CSS.escape(focusedId)}"]`)?.focus();
  }
  trigger.addEventListener('click', () => {
    if (popup.matches(':popover-open')) { popup.hidePopover(); return; }
    renderCalls();
    popup.showPopover();
    const rect = trigger.getBoundingClientRect();
    popup.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - popup.offsetWidth - 8))}px`;
    popup.style.top = `${Math.max(8, Math.min(rect.bottom + 6, innerHeight - popup.offsetHeight - 8))}px`;
    popup.querySelector('button')?.focus();
  });
  root.append(trigger, popup);
  return {
    root,
    /** Refresh the count without replacing the trigger or disturbing keyboard focus. */
    update(items) {
      calls = items.filter(item => item.kind === 'tool');
      const skills = calls.filter(item => item.name === 'Skill').length;
      trigger.textContent = `工具 ${calls.length - skills}${skills ? ` · Skill ${skills}` : ''}`;
      trigger.disabled = calls.length === 0;
      const nextSignature = JSON.stringify(calls.map(item => [item.toolUseId, callLabel(item), item.state]));
      if (nextSignature !== signature && popup.matches(':popover-open')) renderCalls();
      signature = nextSignature;
    }
  };
}

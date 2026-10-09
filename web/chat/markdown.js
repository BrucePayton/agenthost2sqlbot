import {renderMessageContent} from '../embed/assistant-markdown.mjs';

// Keep source text separate from rendered DOM, including unfinished Markdown tokens.
const sources = new WeakMap();
export function render(element, role, content) {
  const source = String(content ?? '');
  if (role === 'assistant') sources.set(element, source);
  renderMessageContent(element, role, source);
}
export function append(element, delta) {
  render(element, 'assistant', (sources.get(element) || '') + String(delta ?? ''));
}

import MarkdownIt from "markdown-it";

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

export function renderAssistantMarkdown(content) {
  return markdown.render(String(content ?? ""));
}

export function renderMessageContent(element, role, content) {
  if (role === "assistant") {
    element.innerHTML = renderAssistantMarkdown(content);
    return;
  }
  element.textContent = String(content ?? "");
}

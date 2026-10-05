/**
 * 复制文本，带降级：Clipboard API 需要安全上下文（HTTPS 或 localhost），
 * 嵌入 http 父页时 navigator.clipboard 会缺失，此时退回 execCommand('copy')。
 * 两条路都失败时保留首个失败的 name/message，便于区分权限、焦点与安全上下文问题。
 */
export async function copyTextWithFallback(text, options = {}) {
  const clipboard =
    options.clipboard !== undefined
      ? options.clipboard
      : globalThis.navigator && globalThis.navigator.clipboard;
  const doc = options.doc || globalThis.document;
  let firstName = "NotSupportedError";
  let firstMessage = "Clipboard API 不可用（需要 HTTPS 或 localhost）";

  if (clipboard && typeof clipboard.writeText === "function") {
    try {
      await clipboard.writeText(text);
      return "clipboard";
    } catch (error) {
      firstName = error?.name || "Error";
      firstMessage = error?.message || String(error);
    }
  }

  if (doc && typeof doc.execCommand === "function") {
    const area = doc.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.opacity = "0";
    doc.body.appendChild(area);
    try {
      area.focus();
      area.select();
      if (doc.execCommand("copy")) return "exec-command";
    } finally {
      doc.body.removeChild(area);
    }
  }

  throw new Error(`复制失败（${firstName}）：${firstMessage}`);
}

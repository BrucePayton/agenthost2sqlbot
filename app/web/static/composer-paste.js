"use strict";

(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ComposerPaste = api;
})(typeof window === "undefined" ? null : window, function buildModule() {
  const extensionByMime = new Map([
    ["image/png", "png"],
    ["image/jpeg", "jpg"],
    ["image/gif", "gif"],
    ["image/webp", "webp"],
  ]);

  function pad(value) { return String(value).padStart(2, "0"); }

  function timestamp(date) {
    return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`
      + `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
  }

  function hasMeaningfulName(file) {
    const name = String(file.name || "").trim();
    return Boolean(name) && !/^(?:image|blob)(?:\.[a-z0-9]+)?$/iu.test(name);
  }

  function clipboardName(file, index, date) {
    if (hasMeaningfulName(file)) return file;
    const extension = extensionByMime.get(file.type) || "img";
    return new File(
      [file],
      `clipboard-${timestamp(date)}-${index + 1}.${extension}`,
      {type: file.type, lastModified: file.lastModified || date.getTime()},
    );
  }

  function extractImageFiles(clipboardData, now = new Date()) {
    const files = [];
    for (const candidate of Array.from(clipboardData?.items || [])) {
      if (candidate.kind !== "file" || !candidate.type.startsWith("image/")) continue;
      const file = candidate.getAsFile();
      if (file) files.push(clipboardName(file, files.length, now));
    }
    return files;
  }

  function createController({
    input,
    canUpload,
    getRemainingSlots,
    uploadFiles,
    onError,
    now = () => new Date(),
  }) {
    async function handlePaste(event) {
      const files = extractImageFiles(event.clipboardData, now());
      if (!files.length) return false;
      event.preventDefault();
      if (!canUpload()) {
        onError("当前不能上传图片。");
        return true;
      }
      const remaining = Math.max(0, Number(getRemainingSlots()) || 0);
      if (files.length > remaining) {
        onError(`最多还能添加 ${remaining} 个附件。`);
        return true;
      }
      try {
        await uploadFiles(files);
      } catch (error) {
        onError(error?.message || "图片上传失败。");
      }
      return true;
    }

    const listener = (event) => { void handlePaste(event); };
    input.addEventListener("paste", listener);
    return {
      handlePaste,
      destroy() { input.removeEventListener("paste", listener); },
    };
  }

  return {extractImageFiles, createController};
});

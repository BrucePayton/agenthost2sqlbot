// UI-only bindings for the unchanged SQLBot Table renderer.
export const i18n = {global: {t: key => key === 'qa.copied' ? '已复制' : key}};
export const ElMessage = {success(text) {
  document.dispatchEvent(new CustomEvent('sqlbot-chart-notice', {detail: text}));
}};

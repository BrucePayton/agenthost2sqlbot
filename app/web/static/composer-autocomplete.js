"use strict";

(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ComposerAutocomplete = api;
})(typeof window === "undefined" ? null : window, function buildModule() {
  function findTrigger(value, caret) {
    const before = value.slice(0, caret);
    const skill = before.match(/^(\s*)\/([A-Za-z0-9._-]*)$/u);
    if (skill) {
      return {
        kind: "skill",
        query: skill[2],
        start: skill[1].length,
        end: caret,
      };
    }

    const file = before.match(/(?:^|\s)@([^\s@"]*)$/u);
    if (!file) return null;
    const tokenLength = file[1].length + 1;
    return {
      kind: "file",
      query: file[1],
      start: caret - tokenLength,
      end: caret,
    };
  }

  function formatFileToken(path) {
    return /[\s"\\]/u.test(path) ? `@${JSON.stringify(path)}` : `@${path}`;
  }

  function replaceTrigger(value, trigger, replacement) {
    const updated = value.slice(0, trigger.start) + replacement + value.slice(trigger.end);
    return {value: updated, caret: trigger.start + replacement.length};
  }

  function isCompleteFileToken(token) {
    if (typeof token !== "string" || token.length < 2 || token[0] !== "@") {
      return false;
    }
    if (token[1] !== '"') return !/[\s"\\]/u.test(token.slice(1));
    try {
      return typeof JSON.parse(token.slice(1)) === "string";
    } catch {
      return false;
    }
  }

  function containsCompleteToken(value, token) {
    if (!isCompleteFileToken(token)) return false;
    let start = value.indexOf(token);
    while (start !== -1) {
      const end = start + token.length;
      const hasLeftBoundary = start === 0 || /\s/u.test(value[start - 1]);
      const hasRightBoundary = end === value.length || /\s/u.test(value[end]);
      if (hasLeftBoundary && hasRightBoundary) return true;
      start = value.indexOf(token, start + 1);
    }
    return false;
  }

  function syncReferences(value, references) {
    return references.filter((reference) => (
      containsCompleteToken(value, reference.token)
    ));
  }

  function createController({input, menu, status, searchFiles, onError}) {
    let sessionId = null;
    let skills = [];
    let trigger = null;
    let items = [];
    let activeIndex = -1;
    let references = [];
    let requestNumber = 0;
    let searchTimer = null;
    let fileRequestController = null;
    let composing = false;
    let fileReferencesUnavailable = false;

    if (!menu.id) {
      menu.id = `${input.id || "composer"}-autocomplete-listbox`;
    }
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    function clearSearchTimer() {
      if (searchTimer !== null) {
        clearTimeout(searchTimer);
        searchTimer = null;
      }
    }

    function invalidateSearches() {
      clearSearchTimer();
      fileRequestController?.abort();
      fileRequestController = null;
      requestNumber += 1;
    }

    function clearMenu({clearStatus = true} = {}) {
      items = [];
      activeIndex = -1;
      menu.replaceChildren();
      menu.hidden = true;
      input.removeAttribute("aria-controls");
      input.removeAttribute("aria-activedescendant");
      if (clearStatus) status.textContent = "";
    }

    function updateActiveOption() {
      for (let index = 0; index < menu.children.length; index += 1) {
        menu.children[index].setAttribute(
          "aria-selected",
          index === activeIndex ? "true" : "false",
        );
      }
      const activeOption = menu.children[activeIndex];
      if (activeOption) {
        input.setAttribute("aria-activedescendant", activeOption.id);
        activeOption.scrollIntoView?.({block: "nearest"});
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function selectItem(index) {
      const item = items[index];
      const currentTrigger = trigger;
      if (!item || !currentTrigger) return;

      let replacement;
      if (item.kind === "skill") {
        replacement = `/${item.name} `;
      } else {
        replacement = formatFileToken(item.path);
      }
      const updated = replaceTrigger(input.value, currentTrigger, replacement);
      input.value = updated.value;
      input.setSelectionRange(updated.caret, updated.caret);

      if (
        item.kind === "file"
        && !references.some((reference) => reference.path === item.path)
      ) {
        references.push({token: replacement, path: item.path});
      }
      references = syncReferences(input.value, references);
      trigger = null;
      invalidateSearches();
      clearMenu();
      input.focus();
    }

    function renderItems(nextItems) {
      items = nextItems;
      activeIndex = items.length > 0 ? 0 : -1;
      menu.replaceChildren();

      for (let index = 0; index < items.length; index += 1) {
        const item = items[index];
        const option = menu.ownerDocument.createElement("div");
        option.id = `${menu.id}-option-${index}`;
        option.setAttribute("role", "option");
        option.setAttribute("tabindex", "-1");
        const label = item.kind === "skill" ? item.name : item.path;
        if (item.kind === "skill") {
          option.className = (
            "composer-autocomplete-option composer-autocomplete-skill"
          );
          const name = menu.ownerDocument.createElement("span");
          name.className = "composer-autocomplete-name";
          name.textContent = label;
          option.appendChild(name);
          if (item.description) {
            const description = menu.ownerDocument.createElement("span");
            description.className = "composer-autocomplete-description";
            description.textContent = item.description;
            description.setAttribute("title", item.description);
            option.appendChild(description);
          }
        } else {
          option.className = "composer-autocomplete-option";
          option.textContent = item.description
            ? `${label}\n${item.description}`
            : label;
        }
        option.addEventListener("pointerdown", (event) => {
          event.preventDefault();
          selectItem(index);
        });
        menu.appendChild(option);
      }

      if (items.length === 0) {
        menu.hidden = true;
        input.removeAttribute("aria-controls");
        input.removeAttribute("aria-activedescendant");
        return;
      }
      status.textContent = "";
      menu.hidden = false;
      input.setAttribute("aria-controls", menu.id);
      updateActiveOption();
    }

    function renderSkills(query) {
      const needle = query.toLocaleLowerCase();
      const matches = skills
        .filter((skill) => {
          const name = String(skill.name ?? "").toLocaleLowerCase();
          const description = String(skill.description ?? "").toLocaleLowerCase();
          return name.includes(needle) || description.includes(needle);
        })
        .map((skill) => ({
          kind: "skill",
          name: String(skill.name),
          description: skill.description ? String(skill.description) : "",
        }));
      if (matches.length === 0) {
        clearMenu();
        status.textContent = "没有匹配技能";
        return;
      }
      renderItems(matches);
    }

    function scheduleFileSearch(query) {
      invalidateSearches();
      const currentRequest = requestNumber;
      const currentSessionId = sessionId;
      clearMenu();
      status.textContent = "正在搜索";

      searchTimer = setTimeout(async () => {
        searchTimer = null;
        const controller = new AbortController();
        fileRequestController = controller;
        try {
          const response = await searchFiles(query, controller.signal);
          if (fileRequestController === controller) fileRequestController = null;
          if (
            currentRequest !== requestNumber
            || currentSessionId !== sessionId
          ) return;
          const results = Array.isArray(response) ? response : response?.items ?? [];
          if (results.length === 0) {
            clearMenu();
            status.textContent = "没有匹配文件";
            return;
          }
          renderItems(results.map((item) => ({
            kind: "file",
            path: String(item.path),
            description: item.description ? String(item.description) : "",
          })));
        } catch (error) {
          if (fileRequestController === controller) fileRequestController = null;
          if (error?.name === "AbortError") return;
          if (
            currentRequest !== requestNumber
            || currentSessionId !== sessionId
          ) return;
          clearMenu();
          trigger = null;
          if (error?.code === "file_reference_unavailable") {
            if (fileReferencesUnavailable) return;
            fileReferencesUnavailable = true;
            status.textContent = "文件引用不可用";
            onError(error);
            return;
          }
          status.textContent = "搜索文件失败";
          onError(error);
        }
      }, 150);
    }

    function handleInput() {
      references = syncReferences(input.value, references);
      if (composing) return;

      const caret = input.selectionStart ?? input.value.length;
      const nextTrigger = findTrigger(input.value, caret);
      if (!nextTrigger) {
        trigger = null;
        invalidateSearches();
        clearMenu();
        return;
      }
      trigger = nextTrigger;
      if (trigger.kind === "skill") {
        invalidateSearches();
        renderSkills(trigger.query);
        return;
      }
      if (fileReferencesUnavailable) {
        trigger = null;
        clearMenu({clearStatus: false});
        return;
      }
      scheduleFileSearch(trigger.query);
    }

    function handleKeydown(event) {
      if (composing || event.isComposing || event.keyCode === 229) return false;
      if (event.key === "Escape" && trigger) {
        event.preventDefault();
        trigger = null;
        invalidateSearches();
        clearMenu();
        return true;
      }
      if (menu.hidden || items.length === 0) return false;

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        activeIndex = (activeIndex + direction + items.length) % items.length;
        updateActiveOption();
        return true;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        selectItem(activeIndex);
        return true;
      }
      return false;
    }

    function reset({keepSkills = false} = {}) {
      invalidateSearches();
      trigger = null;
      references = [];
      composing = false;
      if (!keepSkills) {
        sessionId = null;
        skills = [];
      }
      clearMenu();
    }

    function setSession(nextSession) {
      invalidateSearches();
      sessionId = nextSession.sessionId;
      skills = Array.isArray(nextSession.skills) ? nextSession.skills.slice() : [];
      trigger = null;
      references = [];
      composing = false;
      fileReferencesUnavailable = false;
      clearMenu();
    }

    function setSkills(nextSession) {
      if (nextSession?.sessionId !== sessionId) return;
      skills = Array.isArray(nextSession.skills) ? nextSession.skills.slice() : [];
      if (trigger?.kind === "skill") renderSkills(trigger.query);
    }

    function getFileReferences() {
      const paths = [];
      const seen = new Set();
      for (const reference of syncReferences(input.value, references)) {
        if (seen.has(reference.path)) continue;
        seen.add(reference.path);
        paths.push(reference.path);
      }
      return paths;
    }

    function handleCompositionStart() {
      composing = true;
    }

    function handleCompositionEnd() {
      composing = false;
      handleInput();
    }

    function destroy() {
      invalidateSearches();
      trigger = null;
      references = [];
      composing = false;
      clearMenu();
      input.removeEventListener("compositionstart", handleCompositionStart);
      input.removeEventListener("compositionend", handleCompositionEnd);
    }

    input.addEventListener("compositionstart", handleCompositionStart);
    input.addEventListener("compositionend", handleCompositionEnd);

    return {
      setSession,
      setSkills,
      handleInput,
      handleKeydown,
      getFileReferences,
      reset,
      destroy,
    };
  }

  return {
    findTrigger,
    formatFileToken,
    replaceTrigger,
    syncReferences,
    createController,
  };
});

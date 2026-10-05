// 数巢智能体面板的 DOM 接线：把 panel-model 的结果渲染到页面上，并处理
// 尺寸切换、能力卡片、快捷菜单等纯交互。业务状态仍然由 main.js 持有。
import {
  describePageContext,
  effortLabel,
  sessionListModel,
  suggestionsFor,
} from "./panel-model.js";
import { createPanelDragHandle } from "./panel-drag.js";

const SIZE_LABELS = { small: "小", medium: "中", large: "大", fullscreen: "全屏" };

const ABILITY_DETAILS = {
  find: {
    title: "查找数据",
    tag: "先定位入口",
    desc: "根据你的业务目标，在报表中心、数据集市和指标地图里找最合适的数据入口，并判断是否有权限。",
    scope: "找报表、找数据集、找指标、找负责人、找入口路径",
    example: "帮我找到华东回收转化相关数据",
  },
  explain: {
    title: "问数解释",
    tag: "口径可信",
    desc: "解释当前指标的定义与统计口径，说明所属业务体系、业务域、业务模块和所在报表。",
    scope: "指标定义、统计口径、业务归属、所在报表",
    example: "这个成交率是怎么算的",
  },
  analysis: {
    title: "分析诊断",
    tag: "上下文识别",
    desc: "围绕当前页面的指标、筛选和组件，做趋势分析、异常识别、同比环比、维度下钻和原因归因。",
    scope: "趋势、异常、下钻、归因、风险判断",
    example: "分析当前看板销售额下降的原因",
  },
  dashboard: {
    title: "搭建看板",
    tag: "生成草稿",
    desc: "基于合适数据集生成指标卡、趋势图、明细表、筛选器和 AI 解读模块，发布前会先预览确认。",
    scope: "选数据集、生成组件、配置筛选、组件联动、AI 解读",
    example: "基于回收订单数据集搭一个日报看板",
  },
  dataset: {
    title: "查找数据集",
    tag: "先澄清再筛选",
    desc: "把业务描述澄清成明确指标口径，筛出包含这些指标的数据集；选择多个数据集后，还能继续校验关联键、粒度和数据膨胀。",
    scope: "指标澄清、候选数据集、字段覆盖、数据粒度、宽表关联",
    example: "查找华东回收订单从提交到成交的转化指标数据集",
  },
  subscribe: {
    title: "订阅预警",
    tag: "发送前确认",
    desc: "配置日报、周报、阈值预警和推送对象，并在发送或订阅生效前展示内容预览和接收范围。",
    scope: "频率、阈值、收件人、内容预览、暂停或取消",
    example: "给成交订单配置日报订阅和异常提醒",
  },
  space: {
    title: "协同空间",
    tag: "团队资产",
    desc: "创建小组或组织协同空间，配置成员、资产托管、订阅对象和交付流程，沉淀团队可复用数据资产。",
    scope: "空间创建、成员配置、资产托管、空间订阅、交付流程",
    example: "创建华东战区协同空间并放入日报",
  },
  dispatch: {
    title: "任务派发",
    tag: "动作跟进",
    desc: "把分析结论拆成可执行动作，指派负责人、截止时间和数据依据，并保留后续追踪记录。",
    scope: "负责人、动作项、截止时间、数据依据、跟进记录",
    example: "把当前异常分析派发给华东负责人",
  },
};

const COMPOSER_MIN_HEIGHT = 56;
const COMPOSER_MAX_HEIGHT = 260;

export function createPanelUi({
  elements,
  onSelectSession,
  onFillPrompt,
  initialSize,
  onSizeChange,
}) {
  let panelCommands = [];
  // 上次保存的档位从 embedConfig 带进来；不认识的值退回默认。
  let currentSize = SIZE_LABELS[initialSize] ? initialSize : "small";
  let openPopover = null;
  const headerDrag = elements.header ? createPanelDragHandle({
    header: elements.header, send: panel => elements.sendPanelCommand(panel),
  }) : null;

  function closePopovers(except) {
    for (const entry of [
      { trigger: elements.historyTrigger, popover: elements.historyPopover },
      { trigger: elements.sizeMenuTrigger, popover: elements.sizeMenuPopover },
      { trigger: elements.composerModelTrigger, popover: elements.composerModelPopover },
    ]) {
      if (entry.popover === except || !entry.popover) continue;
      entry.popover.hidden = true;
      entry.trigger?.setAttribute("aria-expanded", "false");
    }
    openPopover = except || null;
  }

  function togglePopover(trigger, popover) {
    const next = popover.hidden;
    closePopovers(next ? popover : null);
    popover.hidden = !next;
    trigger.setAttribute("aria-expanded", String(next));
  }

  function applySize(size, { notifyParent = true } = {}) {
    if (!SIZE_LABELS[size]) return;
    currentSize = size;
    headerDrag?.setEnabled(panelCommands.includes("drag") && currentSize !== "fullscreen");
    elements.panel.dataset.size = size;
    elements.currentSizeLabel.textContent = SIZE_LABELS[size];
    for (const button of elements.sizeOptions) {
      button.classList.toggle("active", button.dataset.sizeOption === size);
    }
    if (notifyParent && panelCommands.includes("resize")) {
      try {
        elements.sendPanelCommand({ action: "resize", size });
      } catch {
        // 父页拒绝或尚未就绪时保持面板内部布局即可。
      }
    }
  }

  function fillPrompt(text) {
    elements.input.value = text;
    elements.input.focus();
    onFillPrompt?.(text);
  }

  function showAbilityDetail(key) {
    const detail = ABILITY_DETAILS[key];
    if (!detail) return;
    elements.abilityDetailTitle.textContent = detail.title;
    elements.abilityDetailTag.textContent = detail.tag;
    elements.abilityDetailDesc.textContent = detail.desc;
    elements.abilityDetailScope.textContent = detail.scope;
    elements.abilityDetailExample.textContent = detail.example;
    elements.abilityDetail.hidden = false;
    for (const button of elements.abilityClusters) {
      button.classList.toggle("active", button.dataset.ability === key);
    }
  }

  function renderSessionButtons(container, model, emptyElement) {
    container.replaceChildren(
      ...model.map((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `rail-task${item.active ? " active" : ""}`;
        button.dataset.sessionId = item.id;
        const title = document.createElement("b");
        title.textContent = item.title;
        const time = document.createElement("small");
        time.textContent = item.timeLabel;
        button.append(title, time);
        button.addEventListener("click", () => {
          closePopovers(null);
          if (!item.active) onSelectSession?.(item.id);
        });
        return button;
      }),
    );
    if (emptyElement) emptyElement.hidden = model.length > 0;
  }

  const ui = {
    /** 父页声明的面板能力，决定尺寸菜单和关闭按钮是否可用。 */
    setPanelCommands(commands) {
      panelCommands = Array.isArray(commands) ? commands : [];
      headerDrag?.setEnabled(panelCommands.includes("drag") && currentSize !== "fullscreen");
      elements.sizeMenu.hidden = !panelCommands.includes("resize");
      elements.closeButton.hidden = !panelCommands.includes("close");
      // 重新握手后把面板尺寸重新广播一次：iframe 单独重载时宿主容器可能还停在
      // 上一次的档位，以 iframe 内部状态为准把两边拉回一致。
      applySize(currentSize, { notifyParent: !elements.sizeMenu.hidden });
    },

    /** Davinci 页面上下文：上下文条、建议卡与 inspector 的数据来源。 */
    setPageContext(state) {
      const context = describePageContext(state);
      elements.contextBar.hidden = !context.available;
      elements.contextSuggestionCard.hidden = !context.available;
      if (!context.available) {
        elements.inspectorContext.textContent = "—";
        elements.inspectorNav.textContent = "—";
        return;
      }
      elements.contextArea.textContent = context.area;
      elements.contextName.textContent = context.name;
      elements.contextSep.hidden = !context.name;
      elements.sessionContextName.textContent = context.name || context.area;
      elements.contextSuggestionHint.textContent = context.name
        ? `，可以直接围绕它继续提问。`
        : `，可以直接开始提问。`;
      elements.inspectorContext.textContent = context.area;
      elements.inspectorNav.textContent = state?.page?.route || context.area;
      elements.suggestedQuestionList.replaceChildren(
        ...suggestionsFor(context.kind).map((prompt) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "suggested-question";
          button.textContent = prompt;
          button.addEventListener("click", () => fillPrompt(prompt));
          return button;
        }),
      );
    },

    setSessions(sessions, activeId) {
      const model = sessionListModel(sessions, activeId);
      renderSessionButtons(elements.railTaskList, model, elements.railEmpty);
      renderSessionButtons(elements.historyList, model, elements.historyEmpty);
      const active = model.find((item) => item.active);
      elements.inspectorObject.textContent = active ? active.title : "等待委托";
    },

    /** 有对话内容时切到任务态，否则回到空闲态引导。 */
    setHasConversation(hasConversation) {
      elements.idleView.hidden = hasConversation;
      elements.taskView.hidden = !hasConversation;
    },

    setInspectorStatus(text) {
      elements.inspectorStatus.textContent = text;
    },

    syncComposerLabels() {
      const model = elements.modelSelect.value;
      elements.composerModelTrigger.hidden = !model;
      elements.composerModelLabel.textContent = model;
      elements.quickModelValue.textContent = model;
      const effort = effortLabel(elements.effortSelect.value);
      elements.composerReasoningLabel.textContent = effort;
      elements.quickReasoningValue.textContent = effort;
      elements.quickModelSubmenu.replaceChildren(
        ...[...elements.modelSelect.options].map((option) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = `quick-menu-option${option.value === model ? " active" : ""}`;
          const name = document.createElement("span");
          name.textContent = option.textContent;
          button.append(name);
          button.addEventListener("click", () => {
            elements.modelSelect.value = option.value;
            ui.syncComposerLabels();
            closePopovers(null);
          });
          return button;
        }),
      );
      elements.quickReasoningSubmenu.replaceChildren(
        ...[...elements.effortSelect.options].map((option) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = `quick-menu-option${
            option.value === elements.effortSelect.value ? " active" : ""
          }`;
          button.textContent = option.textContent;
          button.addEventListener("click", () => {
            elements.effortSelect.value = option.value;
            ui.syncComposerLabels();
            closePopovers(null);
          });
          return button;
        }),
      );
    },

    fillPrompt,
  };

  elements.historyTrigger.addEventListener("click", () =>
    togglePopover(elements.historyTrigger, elements.historyPopover));
  elements.sizeMenuTrigger.addEventListener("click", () =>
    togglePopover(elements.sizeMenuTrigger, elements.sizeMenuPopover));
  elements.composerModelTrigger.addEventListener("click", () =>
    togglePopover(elements.composerModelTrigger, elements.composerModelPopover));

  for (const button of elements.sizeOptions) {
    button.addEventListener("click", () => {
      const size = button.dataset.sizeOption;
      // 只有用户主动换档才算偏好变化；握手重播不算。
      const changed = SIZE_LABELS[size] && size !== currentSize;
      applySize(size);
      if (changed) onSizeChange?.(size);
      closePopovers(null);
    });
  }

  elements.closeButton.addEventListener("click", () => {
    if (!panelCommands.includes("close")) return;
    try {
      elements.sendPanelCommand({ action: "close" });
    } catch {
      // 父页不支持关闭时忽略。
    }
  });

  for (const button of elements.abilityClusters) {
    button.addEventListener("click", () => showAbilityDetail(button.dataset.ability));
  }

  for (const button of elements.promptButtons) {
    button.addEventListener("click", () => {
      fillPrompt(button.dataset.prompt || button.dataset.inspectorPrompt || "");
      elements.capabilityDrawer.hidden = true;
    });
  }

  elements.closeCapabilityDrawer.addEventListener("click", () => {
    elements.capabilityDrawer.hidden = true;
  });

  for (const row of elements.quickMenuRows) {
    row.addEventListener("click", () => {
      const target = row.dataset.quickMenu;
      for (const other of elements.quickMenuRows) {
        other.classList.toggle("active", other === row);
      }
      elements.quickModelSubmenu.hidden = target !== "model";
      elements.quickReasoningSubmenu.hidden = target !== "reasoning";
    });
  }

  elements.input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    elements.form.requestSubmit();
  });

  elements.composerResizeHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = elements.input.getBoundingClientRect().height;
    const onMove = (move) => {
      const height = Math.min(
        COMPOSER_MAX_HEIGHT,
        Math.max(COMPOSER_MIN_HEIGHT, startHeight + (startY - move.clientY)),
      );
      elements.input.style.height = `${height}px`;
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  document.addEventListener("click", (event) => {
    if (!openPopover) return;
    const trigger = event.target.closest?.(
      "#headerTaskHistory, #sizeMenuTrigger, #composerModelTrigger",
    );
    if (trigger || openPopover.contains(event.target)) return;
    closePopovers(null);
  });

  applySize(currentSize, { notifyParent: false });
  return ui;
}

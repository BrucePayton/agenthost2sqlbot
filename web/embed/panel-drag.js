const INTERACTIVE = "button, a, input, textarea, select, summary, [role='button'], [role='menu'], [contenteditable], .head-popover";
const DRAG_THRESHOLD = 5;

/** Send pointer gestures to the owner of the outer window, without moving the iframe DOM. */
export function createPanelDragHandle({ header, send }) {
  let enabled = false;
  let drag = null;
  let frame = null;

  /** Screen coordinates avoid accumulating feedback as the parent moves the iframe. */
  const command = (phase, point) => ({
    action: "drag", phase, pointerId: drag.pointerId,
    screenX: point.x, screenY: point.y,
  });

  /** End or cancel exactly once, including a lost pointer capture. */
  function finish(cancelled, event) {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    if (frame !== null) window.cancelAnimationFrame(frame);
    frame = null;
    const current = drag;
    if (current.started) {
      const point = event ? { x: event.screenX, y: event.screenY } : current.last;
      try { send(command(cancelled ? "cancel" : "end", point)); } catch {}
    }
    drag = null;
    header.removeAttribute("data-dragging");
    if (header.hasPointerCapture?.(current.pointerId)) {
      header.releasePointerCapture(current.pointerId);
    }
  }

  /** Preserve all title-bar controls; only its noninteractive surface starts a gesture. */
  function onDown(event) {
    if (!enabled || drag || event.button !== 0 || event.target.closest(INTERACTIVE)) return;
    event.preventDefault();
    const point = { x: event.screenX, y: event.screenY };
    drag = { pointerId: event.pointerId, origin: point, last: point, started: false };
    header.setPointerCapture?.(event.pointerId);
  }

  /** Coalesce mouse motion so the bridge and parent render at most once per frame. */
  function onMove(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.last = { x: event.screenX, y: event.screenY };
    if (!drag.started) {
      if (Math.hypot(drag.last.x - drag.origin.x, drag.last.y - drag.origin.y) < DRAG_THRESHOLD) return;
      try { send(command("start", drag.origin)); } catch { finish(true); return; }
      drag.started = true;
      header.setAttribute("data-dragging", "true");
    }
    if (frame === null) frame = window.requestAnimationFrame(() => {
      frame = null;
      if (!drag) return;
      try { send(command("move", drag.last)); } catch { finish(true); }
    });
  }

  const onUp = event => finish(false, event);
  const onCancel = event => finish(true, event);
  const onBlur = () => finish(true);
  header.addEventListener("pointerdown", onDown);
  header.addEventListener("pointermove", onMove);
  header.addEventListener("pointerup", onUp);
  header.addEventListener("pointercancel", onCancel);
  header.addEventListener("lostpointercapture", onCancel);
  window.addEventListener("blur", onBlur);

  /** Release global listeners when this iframe goes away. */
  function destroy() {
    finish(true);
    header.removeEventListener("pointerdown", onDown);
    header.removeEventListener("pointermove", onMove);
    header.removeEventListener("pointerup", onUp);
    header.removeEventListener("pointercancel", onCancel);
    header.removeEventListener("lostpointercapture", onCancel);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("pagehide", destroy);
  }
  window.addEventListener("pagehide", destroy);
  return {
    /** Capability negotiation and fullscreen mode decide whether the header can move. */
    setEnabled(value) {
      enabled = value === true;
      if (!enabled) finish(true);
      header.toggleAttribute("data-draggable", enabled);
    },
    destroy,
  };
}

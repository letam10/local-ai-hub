// Canvas interaction for the declarative Image & Mask Studio.
//
// This module never rasterizes or uploads pixels.  It converts pointer input
// into bounded normalized brush geometry and lets app.js persist it through
// the server-owned opaque-ID contract.

const clamp = (value, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, value));

export const normalizedMaskPoint = (clientX, clientY, rect) => {
  const width = Number(rect?.width || 0);
  const height = Number(rect?.height || 0);
  if (!Number.isFinite(clientX) || !Number.isFinite(clientY) || width <= 0 || height <= 0) return null;
  const left = Number(rect.left || 0);
  const top = Number(rect.top || 0);
  return {
    x: Number(clamp((clientX - left) / width).toFixed(6)),
    y: Number(clamp((clientY - top) / height).toFixed(6)),
  };
};

export const compactMaskStroke = (points, maximum = 160) => {
  if (!Array.isArray(points) || !Number.isInteger(maximum) || maximum < 1) return [];
  const result = [];
  for (const raw of points) {
    const x = Number(raw?.x); const y = Number(raw?.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const point = { x: Number(clamp(x).toFixed(6)), y: Number(clamp(y).toFixed(6)) };
    const previous = result.at(-1);
    if (previous && Math.hypot(point.x - previous.x, point.y - previous.y) < 0.003) continue;
    result.push(point);
    if (result.length >= maximum) break;
  }
  return result;
};

const numericControl = (session, selector, fallback, minimum, maximum) => {
  const value = Number(session?.querySelector(selector)?.value);
  return Number.isFinite(value) ? clamp(value, minimum, maximum) : fallback;
};

const brushSettings = (canvas) => {
  const session = canvas.closest("[data-image-mask-session]");
  const mode = session?.querySelector("[data-mask-brush-mode]")?.value === "subtract" ? "subtract" : "add";
  return {
    mode,
    size: numericControl(session, "[data-mask-brush-size]", 0.06, 0.002, 1),
    strength: numericControl(session, "[data-mask-brush-strength]", 1, 0.01, 1),
  };
};

/** Mount pointer listeners and return an idempotent disposer. */
export const mountImageMaskCanvases = (root, { onStroke = async () => {}, onCancel = () => {} } = {}) => {
  if (!root?.querySelectorAll) return () => {};
  const cleanups = [];
  root.querySelectorAll("[data-mask-canvas]").forEach((canvas) => {
    let draft = null;
    let submitting = false;
    const point = (event) => normalizedMaskPoint(event.clientX, event.clientY, canvas.getBoundingClientRect());
    const cancel = () => {
      if (!draft) return;
      try { canvas.releasePointerCapture?.(draft.pointerId); } catch { /* pointer may already be gone */ }
      draft = null;
      canvas.classList.remove("is-painting");
      onCancel(canvas);
    };
    const finish = async (event) => {
      if (!draft || event.pointerId !== draft.pointerId) return;
      const finalPoint = point(event);
      if (finalPoint) draft.points.push(finalPoint);
      const captured = draft;
      cancel();
      const points = compactMaskStroke(captured.points);
      if (!points.length || submitting) return;
      submitting = true;
      canvas.setAttribute("aria-busy", "true");
      try {
        await onStroke({
          studioId: canvas.dataset.studioId || "",
          layerId: canvas.dataset.layerId || "",
          ...brushSettings(canvas),
          points,
        }, canvas);
      } finally {
        submitting = false;
        canvas.removeAttribute("aria-busy");
      }
    };
    const down = (event) => {
      if (submitting || event.button !== 0 || !canvas.dataset.studioId || !canvas.dataset.layerId) return;
      const initial = point(event);
      if (!initial) return;
      event.preventDefault();
      draft = { pointerId: event.pointerId, points: [initial] };
      canvas.classList.add("is-painting");
      canvas.setPointerCapture?.(event.pointerId);
    };
    const move = (event) => {
      if (!draft || event.pointerId !== draft.pointerId) return;
      const next = point(event);
      if (next) draft.points.push(next);
    };
    const keydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", finish);
    canvas.addEventListener("pointercancel", cancel);
    canvas.addEventListener("keydown", keydown);
    cleanups.push(() => {
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", finish);
      canvas.removeEventListener("pointercancel", cancel);
      canvas.removeEventListener("keydown", keydown);
      cancel();
    });
  });
  return () => cleanups.splice(0).forEach((dispose) => dispose());
};

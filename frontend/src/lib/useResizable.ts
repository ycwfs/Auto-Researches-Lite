import { useCallback, useEffect, useRef, useState } from "react";

type Opts = {
  initial: number;
  min: number;
  max: number;
  /** Which edge the drag handle sits on: "right" (e.g. the left sidebar — drag right to
   * grow) or "left" (e.g. the right assistant panel — drag left to grow). */
  edge: "left" | "right";
};

/**
 * A draggable, persisted pixel width for a layout column. Returns the current `width` and
 * an `onPointerDown` to wire to a drag handle; the column flexing to fill the rest needs no
 * state (it stays flex-1). The width is clamped to [min, max] and saved under `storageKey`.
 */
export function useResizable(storageKey: string, { initial, min, max, edge }: Opts) {
  const clamp = useCallback((n: number) => Math.max(min, Math.min(max, n)), [min, max]);

  const [width, setWidth] = useState<number>(() => {
    if (typeof window === "undefined") return initial;
    const v = Number(window.localStorage.getItem(storageKey));
    return v && !Number.isNaN(v) ? Math.max(min, Math.min(max, v)) : initial;
  });

  useEffect(() => {
    window.localStorage.setItem(storageKey, String(Math.round(width)));
  }, [storageKey, width]);

  // Read the latest width at drag start without re-creating the handler each render.
  const widthRef = useRef(width);
  widthRef.current = width;

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = widthRef.current;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      const move = (ev: PointerEvent) => {
        const delta = ev.clientX - startX;
        setWidth(clamp(edge === "right" ? startW + delta : startW - delta));
      };
      const up = () => {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [clamp, edge],
  );

  return { width, setWidth, onPointerDown };
}

/**
 * A thin vertical drag handle between two layout columns. The visible line is 1px; an
 * invisible wider strip on either side makes it easy to grab. Double-click resets via
 * the optional onReset.
 */
export function ResizeHandle({
  onPointerDown,
  onReset,
  className = "",
  title,
}: {
  onPointerDown: (e: React.PointerEvent) => void;
  onReset?: () => void;
  className?: string;
  title?: string;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
      title={title}
      className={`group relative z-20 w-px shrink-0 cursor-col-resize bg-white/[0.06] transition-colors hover:bg-iris-500/60 ${className}`}
    >
      {/* Widen the hit target beyond the 1px visual line. */}
      <span className="absolute inset-y-0 -left-[5px] -right-[5px]" />
    </div>
  );
}

import { Loader2 } from "lucide-react";
import { useRef } from "react";
import { createPortal } from "react-dom";
import { useLang } from "../lib/lang";

export function Spinner({ className = "" }: { className?: string }) {
  return <Loader2 className={`animate-spin ${className}`} size={16} />;
}

export function PageLoader() {
  const { t } = useLang();
  return (
    <div className="flex h-full min-h-[40vh] items-center justify-center text-mist-500">
      <Spinner className="mr-2" /> {t("Loading…", "加载中…")}
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    succeeded: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    ready: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    running: "bg-iris-500/15 text-iris-300 border-iris-500/30 animate-pulse",
    generating: "bg-iris-500/15 text-iris-300 border-iris-500/30 animate-pulse",
    queued: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    created: "bg-white/5 text-mist-300 border-white/10",
    failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
    canceled: "bg-white/5 text-mist-500 border-white/10",
    stopped: "bg-white/5 text-mist-500 border-white/10",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
        map[status] ?? "bg-white/5 text-mist-300 border-white/10"
      }`}
    >
      {status}
    </span>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
      <div
        className="h-full rounded-full bg-gradient-to-r from-iris-500 to-cyan-400 transition-all"
        style={{ width: `${Math.max(2, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      {icon && <div className="text-mist-500">{icon}</div>}
      <div className="text-base font-medium text-mist-100">{title}</div>
      {hint && <div className="max-w-md text-sm text-mist-500">{hint}</div>}
      {action}
    </div>
  );
}

// ---- Design-system atoms (ported from the design handoff) ------------------- //

type Tone = "iris" | "cyan" | "emerald" | "amber" | "rose" | "brand";

const TILE_TONES: Record<Tone, string> = {
  iris: "bg-iris-500/15 text-iris-300",
  cyan: "bg-cyan-500/15 text-cyan-400",
  emerald: "bg-emerald-500/15 text-emerald-300",
  amber: "bg-amber-500/15 text-amber-300",
  rose: "bg-rose-500/15 text-rose-300",
  brand: "bg-gradient-to-br from-iris-500 to-cyan-400 text-white shadow-glow",
};
const TILE_SIZES = { sm: "h-8 w-8", md: "h-9 w-9", lg: "h-11 w-11" };

/** Colored, rounded icon square (e.g. h-9 w-9 rounded-lg bg-iris-500/15 text-iris-300). */
export function IconTile({
  icon,
  tone = "iris",
  size = "md",
  className = "",
}: {
  icon: React.ReactNode;
  tone?: Tone;
  size?: keyof typeof TILE_SIZES;
  className?: string;
}) {
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-lg ${TILE_SIZES[size]} ${TILE_TONES[tone]} ${className}`}
    >
      {icon}
    </div>
  );
}

/** SVG circular gauge for 0–1 values (relevance / score). */
export function Ring({
  value,
  size = 44,
  stroke = 4,
  tone = "cyan",
  label,
}: {
  value: number;
  size?: number;
  stroke?: number;
  tone?: "cyan" | "iris";
  label?: React.ReactNode;
}) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value || 0));
  const toneText = tone === "iris" ? "text-iris-400" : "text-cyan-400";
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} stroke="currentColor" className="text-white/10" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          stroke="currentColor"
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={circ * (1 - v)}
          className={`${toneText} transition-all`}
        />
      </svg>
      {label != null && <span className="absolute font-mono text-[10px] text-mist-300">{label}</span>}
    </div>
  );
}

/** Micro line chart (e.g. a val_bpb trajectory preview). */
export function Sparkline({
  points,
  w = 120,
  h = 28,
  tone = "cyan",
}: {
  points: number[];
  w?: number;
  h?: number;
  tone?: "cyan" | "iris";
}) {
  if (!points.length) return null;
  const min = Math.min(...points);
  const span = Math.max(...points) - min || 1;
  const d = points
    .map((p, i) => `${(i / (points.length - 1 || 1)) * w},${h - ((p - min) / span) * h}`)
    .join(" L ");
  return (
    <svg width={w} height={h} className={tone === "iris" ? "text-iris-400" : "text-cyan-400"}>
      <path d={`M ${d}`} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

export interface SegmentOption<T extends string> {
  id: T;
  label: string;
  icon?: React.ReactNode;
  count?: number;
}

/** Segmented tab control. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className = "",
}: {
  value: T;
  onChange: (v: T) => void;
  options: SegmentOption<T>[];
  className?: string;
}) {
  return (
    <div className={`inline-flex gap-1 rounded-lg border border-white/[0.06] bg-ink-900/40 p-1 text-sm ${className}`}>
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 capitalize transition ${
            value === o.id ? "bg-iris-500/20 text-white" : "text-mist-500 hover:text-mist-100"
          }`}
        >
          {o.icon}
          {o.label}
          {o.count != null && <span className="text-mist-500">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** Overlay + centered card. Click-outside closes.
 *
 * Rendered in a portal on document.body so the fixed overlay is positioned
 * against the viewport — not a transformed ancestor (the page's `animate-fade-up`
 * wrapper keeps a `translateY(0)` transform, which would otherwise become the
 * containing block and let the modal clip off the top of a short page).
 *
 * Width is viewport-relative — `min(maxWidth, 96vw)` — so a wide modal never
 * overflows a narrow window (no occlusion). Height defaults to `max-h-[92vh]`
 * with the card scrolling; pass `height` (e.g. "h-[92vh]") + `bare` for a
 * full-height panel that fills the screen and manages its own internal scroll
 * (the body becomes a flex column, no inner padding). */
export function Modal({
  open,
  onClose,
  children,
  maxWidth = 560,
  height,
  bare = false,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: number;
  height?: string;
  bare?: boolean;
}) {
  // Track where the mouse went DOWN: only a click that both pressed and released on
  // the backdrop closes the modal. This prevents a drag (e.g. selecting text in an
  // input) that starts inside the dialog and releases outside from closing it and
  // discarding the user's input.
  const downOnBackdrop = useRef(false);
  if (!open) return null;
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm sm:p-6"
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downOnBackdrop.current) onClose();
      }}
    >
      <div
        className={`card w-full animate-fade-up ${height ?? "max-h-[92vh]"} ${
          bare ? "flex flex-col overflow-hidden" : "overflow-y-auto p-6"
        }`}
        style={{ maxWidth: `min(${maxWidth}px, 96vw)` }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** Uppercase section eyebrow with optional icon. */
export function Eyebrow({ icon, children }: { icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-[0.08em] text-iris-300">
      {icon}
      {children}
    </div>
  );
}

/** Pulsing cyan "live" indicator. */
export function LiveDot({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-cyan-400">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-400" />
      {label}
    </span>
  );
}

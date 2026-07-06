import { FlaskConical, LayoutGrid, Library, Settings, Sparkles } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../../lib/auth";
import { AssistantProvider, useAssistant } from "../../lib/assistant";
import { useLang } from "../../lib/lang";
import { useResizable } from "../../lib/useResizable";
import { LanguageToggle } from "../LanguageToggle";
import { ResizeHandle } from "../ResizeHandle";
import { ProjectChat } from "../ProjectChat";
import { ThemeToggle } from "../ThemeToggle";

const NAV = [
  { to: "/app", end: true, label: "Dashboard", zh: "仪表盘", icon: LayoutGrid },
  { to: "/app/library", end: false, label: "Library", zh: "文献库", icon: Library },
  { to: "/app/settings", end: false, label: "Settings", zh: "设置", icon: Settings },
];

/** Static local-user chip — the local single-user edition has no accounts, billing, or sign-out. */
function AccountMenu() {
  const { user } = useAuth();
  const { t } = useLang();
  const initials = (user?.full_name || user?.email || "?").slice(0, 2).toUpperCase();

  return (
    <div
      title={user?.email}
      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-iris-500 to-cyan-400 font-display text-xs font-semibold text-white">
        {initials}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-white">
          {user?.full_name || t("Local Researcher", "本地研究者")}
        </span>
        <span className="block truncate text-[11px] text-mist-500">{user?.email}</span>
      </span>
    </div>
  );
}

export function AppShell() {
  return (
    <AssistantProvider>
      <AppShellInner />
    </AssistantProvider>
  );
}

const SIDEBAR_DEFAULT = 240;
const ASSISTANT_DEFAULT = 400;

function AppShellInner() {
  const { lang, t } = useLang();
  const { open, setOpen, projectId } = useAssistant();
  // User-draggable column widths (persisted). The middle column stays flex-1, so it just
  // fills whatever the sidebar + assistant leave.
  const sidebar = useResizable("far-sidebar-w", { initial: SIDEBAR_DEFAULT, min: 184, max: 460, edge: "right" });
  const assistant = useResizable("far-assistant-w", { initial: ASSISTANT_DEFAULT, min: 300, max: 760, edge: "left" });

  return (
    <div className="flex h-screen overflow-hidden bg-ink-950">
      {/* Sidebar */}
      <aside
        style={{ width: sidebar.width }}
        className="flex shrink-0 flex-col border-r border-white/[0.06] bg-ink-900/60"
      >
        <Link
          to="/app"
          title="Go to dashboard"
          className="flex items-center gap-2 px-5 py-5 transition hover:opacity-80"
        >
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-iris-500 to-cyan-400 shadow-glow">
            <FlaskConical size={18} className="text-white" />
          </div>
          <div className="font-display text-sm font-semibold leading-tight">
            Auto-Researches
            <span className="block text-[11px] font-normal text-mist-500">Lite</span>
          </div>
        </Link>

        <nav className="flex flex-1 flex-col space-y-1 px-3 py-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                  isActive
                    ? "bg-iris-500/15 text-white shadow-[inset_0_0_0_1px_rgba(99,102,241,0.3)]"
                    : "text-mist-300 hover:bg-white/[0.05] hover:text-white"
                }`
              }
            >
              <item.icon size={17} />
              {lang === "zh" ? item.zh : item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-white/[0.06] p-3">
          <AccountMenu />
        </div>
      </aside>
      <ResizeHandle
        onPointerDown={sidebar.onPointerDown}
        onReset={() => sidebar.setWidth(SIDEBAR_DEFAULT)}
        title={t("Drag to resize · double-click to reset", "拖动调整宽度 · 双击重置")}
      />

      {/* Main */}
      <main className="relative min-w-0 flex-1 overflow-y-auto">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-aurora opacity-60" />
        <div className="relative mx-auto max-w-6xl px-8 pb-8 pt-6">
          <div className="mb-4 flex justify-end gap-2">
            <LanguageToggle />
            <ThemeToggle />
          </div>
          <Outlet />
          <SiteFooter />
        </div>
      </main>

      {/* Docked project assistant — a flex sibling, so it sits beside the
          content (never overlays it). Only present on a project page. */}
      {open && projectId != null && (
        <ProjectChat
          projectId={projectId}
          onClose={() => setOpen(false)}
          width={assistant.width}
          onResizePointerDown={assistant.onPointerDown}
          onResizeReset={() => assistant.setWidth(ASSISTANT_DEFAULT)}
        />
      )}
    </div>
  );
}

/** Plain footer + a link to the hosted edition. */
function SiteFooter() {
  const { t } = useLang();
  return (
    <footer className="mt-8 border-t border-white/[0.06] py-5 text-center text-xs text-mist-500">
      Auto-Researches Lite
      <span className="mx-2 text-mist-700">·</span>
      <a
        href="https://autoresearches.com/"
        target="_blank"
        rel="noreferrer"
        className="text-mist-400 underline-offset-2 transition hover:text-mist-200 hover:underline"
      >
        {t("Hosted edition", "在线托管版")}
      </a>
    </footer>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  desc,
  action,
}: {
  eyebrow?: string;
  title: string;
  desc?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        {eyebrow && (
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-iris-300">
            <Sparkles size={13} /> {eyebrow}
          </div>
        )}
        <h1 className="font-display text-2xl font-semibold text-white">{title}</h1>
        {desc && <p className="mt-1 max-w-2xl text-sm text-mist-500">{desc}</p>}
      </div>
      {action}
    </div>
  );
}

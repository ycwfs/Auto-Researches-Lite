import { ArrowLeft, BookOpen, Bot, Settings as SettingsIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { api, type Project } from "../../lib/api";
import { useAssistant } from "../../lib/assistant";
import { useLang } from "../../lib/lang";
import { PageLoader } from "../ui";

export function ProjectLayout() {
  const { t } = useLang();
  const { id } = useParams();
  const pid = Number(id);
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const { open: chatOpen, setOpen: setChatOpen, setProjectId } = useAssistant();

  useEffect(() => {
    api
      .getProject(pid)
      .then(setProject)
      .catch(() => navigate("/app"))
      .finally(() => setLoading(false));
  }, [pid, navigate]);

  // Register this project with the docked assistant while mounted; clear the id
  // and hide the panel on leave so it never auto-opens on the next project.
  useEffect(() => {
    setProjectId(pid);
    return () => {
      setProjectId(null);
      setChatOpen(false);
    };
  }, [pid, setProjectId, setChatOpen]);

  if (loading) return <PageLoader />;
  if (!project) return null;

  const pillCls = ({ isActive }: { isActive: boolean }) =>
    `flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
      isActive
        ? "border-iris-400/40 bg-iris-500/15 text-iris-300 shadow-glow"
        : "border-white/[0.06] text-mist-500 hover:text-mist-100"
    }`;

  return (
    <div className="animate-fade-up">
      <div className="mb-4 flex items-center justify-between gap-3">
        <button className="btn-subtle px-2 py-1" onClick={() => navigate("/app")}>
          <ArrowLeft size={15} /> {t("All projects", "全部项目")}
        </button>
        <button
          className={`px-3 py-1.5 ${chatOpen ? "btn-primary" : "btn-ghost"}`}
          onClick={() => setChatOpen(!chatOpen)}
          aria-pressed={chatOpen}
          aria-expanded={chatOpen}
          aria-controls="project-assistant-panel"
        >
          <Bot size={15} /> {t("Assistant", "助手")}
        </button>
      </div>

      <div className="min-w-0">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="font-display text-2xl font-semibold text-mist-100">{project.name}</h1>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {project.categories.map((c) => (
                <span key={c} className="chip">
                  {c}
                </span>
              ))}
              {project.keywords.map((k) => (
                <span key={k} className="chip text-iris-300">
                  #{k}
                </span>
              ))}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <NavLink to="context" title={t("Project context", "项目背景")} className={pillCls}>
              <BookOpen size={15} /> {t("Context", "背景")}
            </NavLink>
            <NavLink to="settings" title={t("Project settings", "项目设置")} className={pillCls}>
              <SettingsIcon size={15} /> {t("Settings", "设置")}
            </NavLink>
          </div>
        </div>

        <Outlet context={{ project, setProject }} />
      </div>
    </div>
  );
}

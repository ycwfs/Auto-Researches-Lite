import { Check, FileText, FolderPlus, Lightbulb, Plus, SlidersHorizontal, Telescope, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { SectionHeading } from "../components/layout/AppShell";
import {
  ConferencesSection,
  PaperSourcesSection,
  PerStepModelSection,
  SchedulesSection,
  SemanticScholarSection,
  useBareVenueMigration,
  useProjectConfigData,
} from "../components/project/configSections";
import { EmptyState, IconTile, Modal, PageLoader, Spinner } from "../components/ui";
import { api, type DiscoverySchedule, type Project, type StepModel } from "../lib/api";
import { useLang } from "../lib/lang";

// arXiv categories offered in the picker (abbreviation + full name).
const ARXIV_CATEGORIES: { code: string; name: string }[] = [
  { code: "cs.AI", name: "Artificial Intelligence" },
  { code: "cs.LG", name: "Machine Learning" },
  { code: "cs.CL", name: "Computation and Language" },
  { code: "cs.CV", name: "Computer Vision and Pattern Recognition" },
  { code: "cs.NE", name: "Neural and Evolutionary Computing" },
  { code: "cs.RO", name: "Robotics" },
  { code: "cs.IR", name: "Information Retrieval" },
  { code: "cs.CR", name: "Cryptography and Security" },
  { code: "cs.DS", name: "Data Structures and Algorithms" },
  { code: "cs.DC", name: "Distributed and Cluster Computing" },
  { code: "cs.HC", name: "Human-Computer Interaction" },
  { code: "cs.SE", name: "Software Engineering" },
  { code: "cs.MA", name: "Multiagent Systems" },
  { code: "cs.SD", name: "Sound" },
  { code: "stat.ML", name: "Machine Learning (Statistics)" },
  { code: "eess.AS", name: "Audio and Speech Processing" },
  { code: "eess.IV", name: "Image and Video Processing" },
  { code: "eess.SP", name: "Signal Processing" },
  { code: "math.OC", name: "Optimization and Control" },
  { code: "q-bio.NC", name: "Neurons and Cognition" },
];

export default function Dashboard() {
  const { t } = useLang();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const load = () => api.listProjects().then(setProjects);
  useEffect(() => {
    void load();
  }, []);

  if (projects === null) return <PageLoader />;

  return (
    <div className="animate-fade-up">
      <SectionHeading
        eyebrow={t("Workspace", "工作区")}
        title={t("Your projects", "你的项目")}
        desc={t(
          "Each project is a research thread: discover papers, summarize them, and read them.",
          "每个项目都是一条研究线索：发现论文、总结论文、阅读论文。",
        )}
        action={
          <button className="btn-primary" onClick={() => setCreating(true)}>
            <Plus size={16} /> {t("New project", "新建项目")}
          </button>
        }
      />

      {projects.length === 0 ? (
        <EmptyState
          icon={<FolderPlus size={32} />}
          title={t("No projects yet", "暂无项目")}
          hint={t(
            "Create your first project to start discovering and summarizing papers.",
            "创建你的第一个项目，开始发现并总结论文。",
          )}
          action={
            <button className="btn-primary mt-2" onClick={() => setCreating(true)}>
              <Plus size={16} /> {t("New project", "新建项目")}
            </button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => navigate(`/app/projects/${p.id}`)}
              className="card group flex h-full flex-col p-5 text-left transition hover:border-iris-400/30 hover:shadow-glow"
            >
              <div className="mb-3 flex items-center justify-between">
                <IconTile icon={<Telescope size={18} />} tone="iris" size="md" />
                <Trash2
                  size={16}
                  className="text-mist-500 opacity-0 transition hover:text-rose-300 group-hover:opacity-100"
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (confirm(t(`Delete "${p.name}"?`, `删除 "${p.name}"？`))) {
                      await api.deleteProject(p.id);
                      void load();
                    }
                  }}
                />
              </div>
              <div className="font-display text-base font-semibold text-white">{p.name}</div>
              <p className="mt-1 line-clamp-2 text-sm text-mist-500">
                {p.description || t("No description", "暂无描述")}
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {p.keywords.slice(0, 3).map((k) => (
                  <span key={k} className="chip text-iris-300">#{k}</span>
                ))}
                {p.categories.slice(0, 2).map((c) => (
                  <span key={c} className="chip">{c}</span>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-4 border-t border-white/[0.06] pt-3 text-xs text-mist-500">
                <span className="inline-flex items-center gap-1.5">
                  <FileText size={13} /> {p.categories.length} {t("categories", "个类别")}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <Lightbulb size={13} /> {p.keywords.length} {t("keywords", "个关键词")}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {creating && (
        <CreateProjectModal
          onClose={() => setCreating(false)}
          onCreated={(p) => navigate(`/app/projects/${p.id}`)}
        />
      )}
    </div>
  );
}

function CreateProjectModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (p: Project) => void;
}) {
  const { t } = useLang();
  // Project details
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [cats, setCats] = useState<string[]>(["cs.AI", "cs.LG"]);
  const [keywords, setKeywords] = useState("");
  const [paperQuery, setPaperQuery] = useState("");
  // Sources + per-source tuning (same controls as project settings).
  const [sources, setSources] = useState<string[]>(["arxiv"]);
  const [srcMax, setSrcMax] = useState<Record<string, number>>({});
  const [pfVenues, setPfVenues] = useState<string[]>([]);
  const [pfMinScore, setPfMinScore] = useState(0);
  const [s2Recency, setS2Recency] = useState(365);
  const [s2MinCit, setS2MinCit] = useState(0);
  const [s2Fields, setS2Fields] = useState("");
  const [disc, setDisc] = useState<DiscoverySchedule>({});
  const [capPapers, setCapPapers] = useState(600);
  const [models, setModels] = useState<Record<string, StepModel>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const { availVenues, availSources, catalog } = useProjectConfigData();
  useBareVenueMigration(availVenues, setPfVenues);
  const pfOn = sources.includes("ai_paper_finder");
  const s2On = sources.includes("semantic_scholar");

  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const toggleCat = (code: string) =>
    setCats((c) => (c.includes(code) ? c.filter((x) => x !== code) : [...c, code]));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const p = await api.createProject({
        name,
        description,
        categories: cats,
        keywords: split(keywords),
        paper_finder_query: paperQuery.trim(),
        paper_sources: sources,
        source_max_results: srcMax,
        paper_finder_venues: pfVenues,
        paper_finder_min_score: pfMinScore,
        s2_recency_days: s2Recency,
        s2_min_citations: s2MinCit,
        s2_fields_of_study: s2Fields,
        discovery_schedule: disc,
        max_total_papers: capPapers,
        step_models: models,
      });
      onCreated(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Failed to create", "创建失败"));
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} maxWidth={720}>
      <h2 className="mb-1 font-display text-lg font-semibold text-white">{t("New project", "新建项目")}</h2>
      <p className="mb-4 text-xs text-mist-500">
        {t(
          "Set everything up now — you can change any of it later in project settings.",
          "现在就把一切配置好——之后都可以在项目设置中修改。",
        )}
      </p>
      <form onSubmit={submit} className="space-y-5">
        {/* Project details */}
        <div className="card p-5">
          <div className="mb-4 flex items-center gap-2.5">
            <IconTile icon={<SlidersHorizontal size={16} />} tone="iris" size="sm" />
            <div className="font-display font-medium text-mist-100">{t("Project details", "项目信息")}</div>
          </div>
          <div className="space-y-3">
            <div>
              <label className="label">{t("Name", "名称")} <span className="text-rose-300">*</span></label>
              <input className="input" required value={name} onChange={(e) => setName(e.target.value)}
                placeholder={t("Efficient attention mechanisms", "高效注意力机制")} />
              <p className="mt-1 text-xs text-mist-500">
                {t(
                  "The project title. With keywords, it scopes arXiv & Semantic Scholar.",
                  "项目标题。它与关键词一起界定 arXiv 和 Semantic Scholar 的检索范围。",
                )}
              </p>
            </div>
            <div>
              <label className="label">{t("Description", "描述")}</label>
              <textarea className="input min-h-[64px]" rows={3} value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t(
                  "Optional — what this project is about. Steers summaries and relevance scoring.",
                  "可选——本项目的主题说明。用于引导摘要与相关性评分。",
                )} />
            </div>
            <div>
              <label className="label">{t("AI Paper Finder Query", "AI Paper Finder 查询")} <span className="text-rose-300">*</span></label>
              <textarea className="input min-h-[96px]" rows={5} required value={paperQuery}
                onChange={(e) => setPaperQuery(e.target.value)}
                placeholder={t(
                  "Paste an abstract (or describe exactly what you're looking for). Sent verbatim to the AI Paper Finder's semantic search — this is what makes the match shine.",
                  "粘贴一段摘要（或准确描述你想找的内容）。它会原样发送给 AI Paper Finder 的语义检索——正是它让匹配效果出色。",
                )} />
              <p className="mt-1 text-xs text-mist-500">
                {t(
                  "Required. Used as-is for semantic retrieval over the conference corpus — independent of the name/keywords (used when the AI Paper Finder source is enabled below).",
                  "必填。原样用于会议语料库上的语义检索——独立于名称/关键词（在下方启用 AI Paper Finder 来源时生效）。",
                )}
              </p>
            </div>
            <div>
              <label className="label">{t("arXiv categories", "arXiv 类别")}</label>
              <div className="max-h-56 overflow-y-auto rounded-lg border border-white/[0.06] bg-ink-900/40 p-2">
                <div className="flex flex-wrap gap-1.5">
                  {ARXIV_CATEGORIES.map((c) => {
                    const on = cats.includes(c.code);
                    return (
                      <button
                        type="button"
                        key={c.code}
                        onClick={() => toggleCat(c.code)}
                        title={c.name}
                        className={`chip ${on ? "text-iris-300 ring-1 ring-iris-400/40" : ""}`}
                      >
                        {on && <Check size={12} />} {c.code}
                        <span className="ml-1 text-mist-500">{c.name}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
              <p className="mt-1 text-xs text-mist-500">{t("With the name, these scope the arXiv search.", "它们与名称一起界定 arXiv 的检索范围。")}</p>
            </div>
            <div>
              <label className="label">{t("Keywords", "关键词")} <span className="text-rose-300">*</span></label>
              <input className="input" required value={keywords} onChange={(e) => setKeywords(e.target.value)}
                placeholder="attention, kv cache" />
              <p className="mt-1 text-xs text-mist-500">
                {t(
                  "Required. With the name, refine arXiv & Semantic Scholar.",
                  "必填。它与名称一起细化 arXiv 和 Semantic Scholar 的检索。",
                )}
              </p>
            </div>
          </div>
        </div>

        <PaperSourcesSection
          availSources={availSources}
          sources={sources}
          setSources={setSources}
          srcMax={srcMax}
          setSrcMax={setSrcMax}
          defaultMax={20}
        />

        {/* Source-specific tuning appears only for the sources you enabled above. */}
        {pfOn && (
          <ConferencesSection
            availVenues={availVenues}
            pfVenues={pfVenues}
            setPfVenues={setPfVenues}
            minScore={pfMinScore}
            setMinScore={setPfMinScore}
          />
        )}
        {s2On && (
          <SemanticScholarSection
            s2Recency={s2Recency}
            setS2Recency={setS2Recency}
            s2MinCit={s2MinCit}
            setS2MinCit={setS2MinCit}
            s2Fields={s2Fields}
            setS2Fields={setS2Fields}
          />
        )}

        <SchedulesSection
          disc={disc}
          setDisc={setDisc}
          capPapers={capPapers}
          setCapPapers={setCapPapers}
        />

        <PerStepModelSection catalog={catalog} models={models} setModels={setModels} />

        {error && <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-300">{error}</div>}
        <div className="sticky bottom-0 -mx-6 flex justify-end gap-2 border-t border-white/[0.06] bg-ink-850/95 px-6 py-3 backdrop-blur">
          <button type="button" className="btn-ghost" onClick={onClose}>{t("Cancel", "取消")}</button>
          <button className="btn-primary"
            disabled={busy || !name.trim() || !keywords.trim() || !paperQuery.trim()}>
            {busy && <Spinner />} {t("Create project", "创建项目")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

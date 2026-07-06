import { Check, SlidersHorizontal, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { IconTile, Spinner } from "../components/ui";
import { useLang } from "../lib/lang";
import { api, type DiscoverySchedule, type Project, type ProjectPrompt, type StepModel } from "../lib/api";
import {
  ConferencesSection,
  PaperSourcesSection,
  PerStepModelSection,
  SchedulesSection,
  SemanticScholarSection,
  useBareVenueMigration,
  useProjectConfigData,
} from "../components/project/configSections";

type Ctx = { project: Project; setProject?: (p: Project) => void };

export default function ProjectSettings() {
  const { t } = useLang();
  const { project, setProject } = useOutletContext<Ctx>();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description || "");
  const [keywords, setKeywords] = useState((project.keywords || []).join(", "));
  const [paperQuery, setPaperQuery] = useState(project.paper_finder_query || "");
  const [sources, setSources] = useState<string[]>(project.paper_sources || ["arxiv"]);
  const [disc, setDisc] = useState<DiscoverySchedule>(project.discovery_schedule || {});
  const [s2Recency, setS2Recency] = useState(project.s2_recency_days ?? 365);
  const [s2Fields, setS2Fields] = useState(project.s2_fields_of_study ?? "");
  const [s2MinCit, setS2MinCit] = useState(project.s2_min_citations ?? 0);
  const [pfVenues, setPfVenues] = useState<string[]>(project.paper_finder_venues || []);
  const [pfMinScore, setPfMinScore] = useState(project.paper_finder_min_score ?? 0);
  const [srcMax, setSrcMax] = useState<Record<string, number>>(project.source_max_results || {});
  const [capPapers, setCapPapers] = useState(project.max_total_papers ?? 600);
  const [models, setModels] = useState<Record<string, StepModel>>(project.step_models || {});
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const { availVenues, availSources, catalog } = useProjectConfigData();
  useBareVenueMigration(availVenues, setPfVenues);
  const pfOn = sources.includes("ai_paper_finder");

  const save = async () => {
    if (!paperQuery.trim()) {
      setErr(t("AI Paper Finder Query is required.", "AI Paper Finder 查询为必填项。"));
      return;
    }
    setErr("");
    setBusy(true);
    setSaved(false);
    try {
      const updated = await api.updateProject(project.id, {
        name: name.trim() || project.name,
        description,
        keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
        paper_finder_query: paperQuery.trim(),
        paper_finder_min_score: pfMinScore,
        paper_sources: sources,
        discovery_schedule: disc,
        step_models: models,
        s2_recency_days: s2Recency,
        s2_fields_of_study: s2Fields,
        s2_min_citations: s2MinCit,
        paper_finder_venues: pfVenues,
        source_max_results: srcMax,
        max_total_papers: capPapers,
      });
      setProject?.(updated); // reflect name/keywords in the header + context immediately
      setSaved(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Project details — name/keywords drive arXiv+S2; the query drives AI Paper Finder */}
      <div className="card p-5">
        <div className="mb-4 flex items-center gap-2.5">
          <IconTile icon={<SlidersHorizontal size={16} />} tone="iris" size="sm" />
          <div className="font-display font-medium text-mist-100">{t("Project details", "项目详情")}</div>
        </div>
        <div className="space-y-3">
          <div>
            <label className="label">{t("Name", "名称")}</label>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("Project name", "项目名称")}
            />
          </div>
          <div>
            <label className="label">{t("Description", "描述")}</label>
            <textarea
              className="input min-h-[72px]"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("What this project is about.", "本项目的相关说明。")}
            />
            <p className="mt-1 text-xs text-mist-500">
              {t(
                "Steers summaries and relevance scoring. Not part of any search query.",
                "用于引导摘要与相关性评分。不参与任何检索查询。",
              )}
            </p>
          </div>
          <div>
            <label className="label">{t("Keywords", "关键词")}</label>
            <input
              className="input"
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder={t("e.g. sparse attention, long context, KV cache", "例如：sparse attention, long context, KV cache")}
            />
            <p className="mt-1 text-xs text-mist-500">
              {t(
                "Comma-separated. With the name, these drive arXiv & Semantic Scholar retrieval — tweak them and re-run discovery to tune the results.",
                "以逗号分隔。与名称一起驱动 arXiv 和 Semantic Scholar 检索——调整后重新运行发现即可微调结果。",
              )}
            </p>
          </div>
          <div>
            <label className="label">
              {t("AI Paper Finder Query", "AI Paper Finder 查询")} <span className="text-rose-300">*</span>
            </label>
            <textarea
              className="input min-h-[96px]"
              rows={5}
              value={paperQuery}
              onChange={(e) => setPaperQuery(e.target.value)}
              placeholder={t(
                "Paste an abstract (or describe exactly what you're looking for). Sent verbatim to the AI Paper Finder's semantic search.",
                "粘贴一段摘要（或准确描述你要查找的内容）。将原样发送给 AI Paper Finder 的语义检索。",
              )}
            />
            <p className="mt-1 text-xs text-mist-500">
              {t(
                "Required. Used as-is for semantic retrieval over the conference corpus — independent of the name/keywords above. Pasting an abstract gives the sharpest matches.",
                "必填项。原样用于在会议语料库上进行语义检索——与上方的名称/关键词无关。粘贴摘要可获得最精准的匹配。",
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
        defaultMax={project.max_results ?? 20}
      />

      <ConferencesSection
        availVenues={availVenues}
        pfVenues={pfVenues}
        setPfVenues={setPfVenues}
        minScore={pfMinScore}
        setMinScore={setPfMinScore}
        dimmed={!pfOn}
      />

      <SemanticScholarSection
        s2Recency={s2Recency}
        setS2Recency={setS2Recency}
        s2MinCit={s2MinCit}
        setS2MinCit={setS2MinCit}
        s2Fields={s2Fields}
        setS2Fields={setS2Fields}
      />

      <SchedulesSection
        disc={disc}
        setDisc={setDisc}
        capPapers={capPapers}
        setCapPapers={setCapPapers}
      />

      <PerStepModelSection catalog={catalog} models={models} setModels={setModels} />

      <div className="flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy}>
          {busy && <Spinner />} {t("Save settings", "保存设置")}
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-300">
            <Check size={15} /> {t("Saved.", "已保存。")}
          </span>
        )}
        {err && <span className="text-sm text-rose-300">{err}</span>}
      </div>

      <PromptsStyleSection projectId={project.id} />
    </div>
  );
}

/** Per-project prompt editing. Each AI step's full template is editable here; edits
 *  are validated server-side (required placeholders must remain) before saving. */
function PromptsStyleSection({ projectId }: { projectId: number }) {
  const { t } = useLang();
  const [prompts, setPrompts] = useState<ProjectPrompt[] | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sync = (ps: ProjectPrompt[]) => {
    setPrompts(ps);
    setDraft(Object.fromEntries(ps.map((p) => [p.key, p.template])));
  };
  const load = () => {
    setError(null);
    api
      .projectPrompts(projectId)
      .then(sync)
      .catch((e) => setError(e instanceof Error ? e.message : t("Failed to load prompt settings.", "加载提示词设置失败。")));
  };
  useEffect(load, [projectId]);

  const save = async () => {
    setBusy(true);
    setSaved(false);
    setError(null);
    try {
      sync(await api.updateProjectPrompts(projectId, draft));
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Failed to save prompts.", "保存提示词失败。"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card p-5">
      <div className="mb-1 flex items-center gap-2">
        <IconTile icon={<Wand2 size={16} />} tone="iris" />
        <h2 className="font-display text-base font-semibold text-white">{t("Prompts", "提示词")}</h2>
      </div>
      <p className="mb-3 text-sm text-mist-500">
        {t("Edit the prompt each AI step runs from — tone, focus, venue conventions, structure. Keep the ", "编辑每个 AI 步骤所使用的提示词——语气、重点、会议惯例、结构。请保留 ")}
        <span className="text-mist-300">{t("required {placeholders}", "每个文本框下列出的必需 {placeholders}")}</span>
        {t(
          " listed under each box (the save is rejected if one is missing) and the stated output format. Reset returns a step to its built-in default.",
          "（缺少任意一个都会导致保存失败）以及规定的输出格式。重置会将某个步骤恢复为其内置默认值。",
        )}
      </p>
      {error && (
        <div className="mb-3 flex items-start gap-3 rounded-lg border border-rose-500/20 bg-rose-500/[0.06] px-3 py-2 text-sm text-rose-200">
          <span className="flex-1">{error}</span>
          {!prompts && (
            <button className="btn-subtle px-2.5 py-1 text-xs" onClick={load}>
              {t("Retry", "重试")}
            </button>
          )}
        </div>
      )}
      <div className="space-y-4">
        {(prompts || []).map((p) => {
          const dirty = (draft[p.key] ?? "") !== p.template;
          const isDefault = (draft[p.key] ?? "") === p.default_template;
          return (
            <div key={p.key} className="rounded-lg border border-white/[0.06] bg-ink-900/30 p-3">
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <span className="font-medium text-white">{p.label}</span>
                <span className="chip text-[10px]">{p.stage}</span>
                {p.is_custom ? (
                  <span className="chip border-amber-500/30 bg-amber-500/10 text-[10px] text-amber-300">
                    {t("Custom", "自定义")}
                  </span>
                ) : (
                  <span className="chip text-[10px]">{t("Default", "默认")}</span>
                )}
                <button
                  className="ml-auto text-xs text-mist-500 hover:text-mist-200 disabled:opacity-40"
                  disabled={isDefault}
                  onClick={() => setDraft((d) => ({ ...d, [p.key]: p.default_template }))}
                >
                  {t("Reset to default", "恢复默认")}
                </button>
              </div>
              <p className="mb-1.5 text-xs text-mist-500">{p.contract_note}</p>
              <textarea
                className="input min-h-[120px] w-full resize-y font-mono text-xs leading-relaxed"
                value={draft[p.key] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, [p.key]: e.target.value }))}
              />
              {Object.keys(p.placeholder_docs).length > 0 && (
                <dl className="mt-1.5 space-y-0.5">
                  {Object.entries(p.placeholder_docs).map(([ph, doc]) => (
                    <div key={ph} className="flex gap-2 text-[11px] leading-relaxed">
                      <dt className="shrink-0 font-mono text-iris-300">
                        {`{${ph}}`}
                        {p.placeholders.includes(ph) && <span className="text-rose-300">*</span>}
                      </dt>
                      <dd className="text-mist-500">{doc}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {dirty && <p className="mt-1 text-[11px] text-amber-300/80">{t("Unsaved changes", "有未保存的更改")}</p>}
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy || !prompts}>
          {busy && <Spinner />} {t("Save prompts", "保存提示词")}
        </button>
        <span className="text-xs text-mist-600">{t("* required placeholder", "* 必需的占位符")}</span>
        {saved && (
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-300">
            <Check size={15} /> {t("Saved.", "已保存。")}
          </span>
        )}
      </div>
    </div>
  );
}

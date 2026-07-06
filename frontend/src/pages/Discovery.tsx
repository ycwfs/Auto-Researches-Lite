import {
  Calendar,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Clock,
  Code,
  ExternalLink,
  FileText,
  Github,
  MinusSquare,
  Play,
  Plus,
  RefreshCw,
  Search,
  Square,
  Telescope,
  Trash2,
  TrendingUp,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  Eyebrow,
  IconTile,
  Modal,
  ProgressBar,
  Ring,
  Segmented,
  Spinner,
  StatusPill,
} from "../components/ui";
import { api, ApiError, type Job, type Paper, type Project, type Trends } from "../lib/api";
import { EntityChatPanel } from "../components/EntityChatPanel";
import { JobLog } from "../components/JobLog";
import { Markdown } from "../components/Markdown";
import { useJobPolling } from "../lib/useJob";
import { useLang } from "../lib/lang";

type Ctx = { project: Project };
type Tab = "papers" | "trends";

// Display labels for the source channel a paper was fetched from. The keys are the
// stable source identifiers; only the display values are localized (most are proper
// nouns kept verbatim, "Uploaded PDF" is the one translated label).
const SOURCE_LABELS: Record<string, { en: string; zh: string }> = {
  arxiv: { en: "arXiv", zh: "arXiv" },
  semantic_scholar: { en: "Semantic Scholar", zh: "Semantic Scholar" },
  ai_paper_finder: { en: "AI Paper Finder", zh: "AI Paper Finder" },
  upload: { en: "Uploaded PDF", zh: "上传的 PDF" },
};

// Resolve a source key to its display label for the active language.
function sourceLabel(source: string, t: (en: string, zh: string) => string): string {
  const entry = SOURCE_LABELS[source];
  return entry ? t(entry.en, entry.zh) : source;
}

// A paper's `arxiv_id` is really the id from whatever source fetched it; only some are
// genuine arXiv ids (arXiv papers + Semantic-Scholar papers that are on arXiv). Match the
// arXiv id shape — new "2401.01234" or old "math.AG/0211159", optional "v2" — so the link
// is labelled "arXiv:…" only for those, not for OpenReview/CVF/S2 hashes.
const ARXIV_ID_RE = /^(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})(v\d+)?$/i;
const isArxivId = (id?: string): boolean => !!id && ARXIV_ID_RE.test(id);

// Format an ISO timestamp as a short local date + time (e.g. "Jun 5, 2026, 14:30").
function fmtDateTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Local-day key for a timestamp, e.g. "2026-06-17" (sortable as a string).
function dayKey(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-CA");
}

// Short label for a day key, e.g. "Jun 17" (adds the year for other years).
function dayLabel(key: string): string {
  const d = new Date(`${key}T12:00:00`);
  if (Number.isNaN(d.getTime())) return key;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export default function Discovery() {
  const { project } = useOutletContext<Ctx>();
  const { t } = useLang();
  const pid = project.id;
  const [tab, setTab] = useState<Tab>("papers");
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selectedPapers, setSelectedPapers] = useState<Set<number>>(new Set());
  const [dateFilter, setDateFilter] = useState<string>("all"); // "all" or a day key
  const [trends, setTrends] = useState<Trends | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [pfJobId, setPfJobId] = useState<number | null>(null); // AI Paper Finder run (concurrent)
  const [zoteroJobId, setZoteroJobId] = useState<number | null>(null); // Zotero sync (async)
  const [lastRunAt, setLastRunAt] = useState<string | null>(null);
  const [uploadMsg, setUploadMsg] = useState("");
  // Whether THIS project has customized the Summary / code-analysis prompts (vs the
  // built-in default). Re-summarizing with the default prompt would just reproduce the
  // shared default, so the per-paper re-summarize buttons only show when a prompt is custom.
  const [summaryPromptCustom, setSummaryPromptCustom] = useState(false);
  const [codePromptCustom, setCodePromptCustom] = useState(false);
  useEffect(() => {
    api
      .projectPrompts(pid)
      .then((ps) => {
        setSummaryPromptCustom(!!ps.find((p) => p.key === "summary_5pt")?.is_custom);
        setCodePromptCustom(!!ps.find((p) => p.key === "code_analysis")?.is_custom);
      })
      .catch(() => {});
  }, [pid]);
  // Add-paper (arXiv link / PDF upload) modal + its own job (so its progress card
  // doesn't collide with the discovery job's).
  const [addOpen, setAddOpen] = useState(false);
  const [addUrl, setAddUrl] = useState("");
  const [addTitle, setAddTitle] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addJobId, setAddJobId] = useState<number | null>(null);
  const addFileRef = useRef<HTMLInputElement | null>(null);

  const loadResults = useCallback(async () => {
    const [p, tr] = await Promise.all([api.papers(pid), api.trends(pid)]);
    setPapers(p);
    setTrends(tr);
  }, [pid]);

  useEffect(() => {
    void loadResults();
  }, [loadResults]);

  // Default to all papers selected for sync; the user unchecks what to exclude.
  useEffect(() => {
    setSelectedPapers(new Set(papers.map((p) => p.id)));
  }, [papers]);

  // Distinct discovery days (newest first) with their paper counts, for the date bar.
  // AI Paper Finder papers are excluded: they have their own source chip, and counting
  // them here too would show every finder paper twice (under its day AND its chip).
  const dateGroups = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of papers) {
      if (p.source === "ai_paper_finder") continue;
      const k = dayKey(p.created_at);
      if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
  }, [papers]);

  // AI Paper Finder papers get their own source filter (its corpus is decoupled from
  // the date-grouped scheduled discovery), shown as a chip next to "All".
  const aipfCount = useMemo(
    () => papers.filter((p) => p.source === "ai_paper_finder").length,
    [papers],
  );

  // Papers shown under the active date filter ("all" shows every paper). If the
  // active day no longer matches any paper (its last one was just deleted), fall
  // back to all papers so the reset effect below is purely cosmetic — never a
  // one-frame "No papers yet" flash.
  // Title search across ALL explored papers (case-insensitive). While a query is
  // active it takes precedence over the date/source chips (which are hidden).
  const [search, setSearch] = useState("");
  const searchQuery = search.trim().toLowerCase();

  const visiblePapers = useMemo(() => {
    if (searchQuery) {
      return papers.filter((p) => (p.title || "").toLowerCase().includes(searchQuery));
    }
    if (dateFilter === "all") return papers;
    if (dateFilter === "aipf") return papers.filter((p) => p.source === "ai_paper_finder");
    const f = papers.filter(
      (p) => p.source !== "ai_paper_finder" && dayKey(p.created_at) === dateFilter,
    );
    return f.length ? f : papers;
  }, [papers, dateFilter, searchQuery]);

  // Drop a filter that no longer matches any paper (e.g. after deleting the last one of a
  // day, or the last AI Paper Finder paper).
  useEffect(() => {
    if (dateFilter === "all") return;
    if (dateFilter === "aipf") {
      if (aipfCount === 0) setDateFilter("all");
    } else if (!dateGroups.some(([k]) => k === dateFilter)) {
      setDateFilter("all");
    }
  }, [dateGroups, dateFilter, aipfCount]);

  const togglePaper = (id: number) =>
    setSelectedPapers((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  // Select-all toggles the currently visible (filtered) papers; selection persists across filters.
  const selectedVisible = visiblePapers.filter((p) => selectedPapers.has(p.id)).length;
  const allVisibleSelected = visiblePapers.length > 0 && selectedVisible === visiblePapers.length;
  const toggleAllPapers = () =>
    setSelectedPapers((s) => {
      const next = new Set(s);
      visiblePapers.forEach((p) => (allVisibleSelected ? next.delete(p.id) : next.add(p.id)));
      return next;
    });

  // Resume the most recent discovery job after navigating back to this
  // panel (or after a page reload). Job state is otherwise lost on unmount, so
  // an in-flight run's progress would disappear when switching project tabs.
  useEffect(() => {
    let cancelled = false;
    api
      .listJobs(pid)
      .then((jobs) => {
        if (cancelled) return;
        const latest = (type: string) => jobs.find((j) => j.type === type);
        const d = latest("discovery");
        const pf = latest("paper_finder");
        if (d) {
          setJobId((cur) => cur ?? d.id);
          // Seed "last run" from the most recent discovery job (newest-first list).
          setLastRunAt((cur) => cur ?? d.updated_at);
        }
        if (pf) setPfJobId((cur) => cur ?? pf.id);
        // Resume an in-flight Zotero sync so its progress survives navigating away.
        const zt = jobs.find(
          (j) => j.type === "zotero_upload" && (j.status === "queued" || j.status === "running"),
        );
        if (zt) setZoteroJobId((cur) => cur ?? zt.id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [pid]);
  const job = useJobPolling(jobId, loadResults);
  const addJob = useJobPolling(addJobId, loadResults);
  const pfJob = useJobPolling(pfJobId, loadResults);
  const zoteroJob = useJobPolling(zoteroJobId, () => setZoteroJobId(null));
  // Refresh "last run" when the current discovery job reaches a terminal state.
  useEffect(() => {
    if (job && (job.status === "succeeded" || job.status === "failed")) {
      setLastRunAt(job.updated_at);
    }
  }, [job?.status, job?.updated_at]);
  // Surface the add-paper job's outcome (the list auto-refreshes via loadResults).
  useEffect(() => {
    if (addJob?.status === "succeeded") setUploadMsg(addJob.log.trim().split("\n").pop() || t("Paper added.", "已添加论文。"));
    else if (addJob?.status === "failed") setUploadMsg(addJob.error || t("Couldn't add the paper.", "无法添加该论文。"));
  }, [addJob?.status]);
  const running = job?.status === "running" || job?.status === "queued";
  const addRunning = addJob?.status === "running" || addJob?.status === "queued";
  const pfRunning = pfJob?.status === "running" || pfJob?.status === "queued";

  const run = async () => {
    setUploadMsg("");
    try {
      const j = await api.runDiscovery(pid);
      setJobId(j.id);
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Failed to start", "启动失败"));
    }
  };

  // Run only the AI Paper Finder (decoupled — its own job, so it runs concurrently with
  // a regular discovery).
  const runPaperFinder = async () => {
    setUploadMsg("");
    try {
      const j = await api.runPaperFinder(pid);
      setPfJobId(j.id);
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Failed to start", "启动失败"));
    }
  };

  // Reset and close the add-paper modal (clears both inputs so neither lingers).
  const closeAdd = () => {
    setAddOpen(false);
    setAddUrl("");
    setAddTitle("");
  };

  const addByUrl = async () => {
    if (!addUrl.trim()) return;
    setAddBusy(true);
    setUploadMsg("");
    try {
      const j = await api.addPaper(pid, addUrl.trim());
      setAddJobId(j.id);
      setUploadMsg(t("Fetching & summarizing the paper…", "正在获取并总结论文……"));
      closeAdd();
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Failed to add paper", "添加论文失败"));
    } finally {
      setAddBusy(false);
    }
  };

  const addByFile = async (f: File) => {
    setAddBusy(true);
    setUploadMsg("");
    try {
      const j = await api.uploadPaper(pid, f, addTitle);
      setAddJobId(j.id);
      setUploadMsg(t("Parsing & summarizing the PDF…", "正在解析并总结 PDF……"));
      closeAdd();
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Failed to upload PDF", "上传 PDF 失败"));
    } finally {
      setAddBusy(false);
    }
  };

  const removePaper = async (id: number) => {
    setPapers((ps) => ps.filter((p) => p.id !== id)); // optimistic
    try {
      await api.deletePaper(pid, id);
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Failed to delete paper", "删除论文失败"));
      void loadResults(); // re-sync on failure
    }
  };

  const syncZotero = async () => {
    setUploadMsg("");
    try {
      // Async job — its progress shows in a JobCard and survives navigating away.
      setZoteroJobId((await api.zoteroUpload(pid, Array.from(selectedPapers))).id);
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Zotero upload failed", "Zotero 上传失败"));
    }
  };

  const removeSelectedPapers = async () => {
    const ids = Array.from(selectedPapers);
    if (ids.length === 0) return;
    if (
      !confirm(
        t(
          `Delete ${ids.length} selected paper${ids.length > 1 ? "s" : ""} from this project? This can't be undone.`,
          `从该项目中删除选中的 ${ids.length} 篇论文？此操作无法撤销。`,
        ),
      )
    )
      return;
    const toDelete = selectedPapers;
    setPapers((ps) => ps.filter((p) => !toDelete.has(p.id))); // optimistic
    setSelectedPapers(new Set());
    try {
      const r = await api.deletePapers(pid, ids);
      setUploadMsg(
        t(
          `Deleted ${r.deleted} paper${r.deleted === 1 ? "" : "s"}.`,
          `已删除 ${r.deleted} 篇论文。`,
        ),
      );
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : t("Failed to delete papers", "删除论文失败"));
      void loadResults(); // re-sync on failure
    }
  };

  // Bulk re-summarize the selected papers with the project's current prompt. One job; on
  // completion bump `summaryReloadKey` so any open card summaries re-fetch the fresh result.
  const [bulkResumJobId, setBulkResumJobId] = useState<number | null>(null);
  const [bulkResumMode, setBulkResumMode] = useState<"full_text" | "code" | null>(null);
  const [summaryReloadKey, setSummaryReloadKey] = useState(0);
  const bulkResumJob = useJobPolling(bulkResumJobId, () => {
    setSummaryReloadKey((k) => k + 1);
    void loadResults();
    setBulkResumJobId(null);
  });
  const bulkResumRunning =
    bulkResumJobId != null &&
    (!bulkResumJob || bulkResumJob.status === "queued" || bulkResumJob.status === "running");
  useEffect(() => {
    if (bulkResumJob?.status === "succeeded")
      setUploadMsg(bulkResumJob.log.trim().split("\n").pop() || t("Re-summarized.", "已重新总结。"));
    else if (bulkResumJob?.status === "failed")
      setUploadMsg(bulkResumJob.error || t("Re-summarize failed.", "重新总结失败。"));
  }, [bulkResumJob?.status]);
  const startBulkResummarize = async (mode: "full_text" | "code") => {
    const ids = Array.from(selectedPapers);
    if (ids.length === 0 || bulkResumRunning) return;
    setBulkResumMode(mode);
    try {
      const j = await api.resummarizePapers(pid, ids, mode);
      setBulkResumJobId(j.id);
      setUploadMsg(
        t(
          `Re-summarizing ${ids.length} selected paper${ids.length > 1 ? "s" : ""}…`,
          `正在重新总结选中的 ${ids.length} 篇论文……`,
        ),
      );
    } catch (e) {
      setBulkResumMode(null);
      setUploadMsg(e instanceof Error ? e.message : t("Failed to start re-summarize", "启动重新总结失败"));
    }
  };

  return (
    <div className="space-y-5">
      {/* Command bar */}
      <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex min-w-[220px] flex-1 items-center gap-3">
          <IconTile icon={<Telescope size={18} />} tone="iris" size="md" />
          <div className="min-w-0">
            <div className="text-sm font-medium text-white">{t("arXiv discovery", "arXiv 发现")}</div>
            <div className="truncate font-mono text-xs text-mist-500">
              {t(
                "Fetch the latest arXiv papers and summarize them.",
                "获取最新的 arXiv 论文并进行总结。",
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-1 font-mono text-[11px] text-mist-500">
              <Clock size={11} />
              {running
                ? t("Running now…", "正在运行……")
                : lastRunAt
                  ? t(`Last run: ${fmtDateTime(lastRunAt)}`, `上次运行：${fmtDateTime(lastRunAt)}`)
                  : t("Not run yet", "尚未运行")}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-ghost" onClick={() => setAddOpen(true)} disabled={addRunning}>
            {addRunning ? <Spinner /> : <Plus size={15} />} {t("Add paper", "添加论文")}
          </button>
          <button className="btn-ghost" onClick={syncZotero} disabled={selectedPapers.size === 0}>
            <Upload size={15} /> {t("Sync", "同步")} {selectedPapers.size > 0 ? `${selectedPapers.size} ` : ""}{t("to Zotero", "到 Zotero")}
          </button>
          <button className="btn-primary" onClick={run} disabled={running}>
            {running ? <Spinner /> : <Play size={15} />}
            {running
              ? t("Running…", "运行中……")
              : papers.length
                ? t("Re-run discovery", "重新运行发现")
                : t("Run discovery", "运行发现")}
          </button>
          {(project.paper_sources || []).includes("ai_paper_finder") && (
            <button
              className="btn-ghost"
              onClick={runPaperFinder}
              disabled={pfRunning}
              title={t(
                "Run only the AI Paper Finder — its corpus is fixed, so it's excluded from scheduled runs and triggered manually here. Runs concurrently with a regular discovery.",
                "仅运行 AI Paper Finder——其语料库是固定的，因此不参与定时运行，需要在此手动触发。可与常规发现并发运行。",
              )}
            >
              {pfRunning ? <Spinner /> : <Telescope size={15} />} {t("Run AI Paper Finder", "运行 AI Paper Finder")}
            </button>
          )}
        </div>
      </div>

      <JobCard job={job} onCancelled={loadResults} />
      <JobCard job={pfJob} onCancelled={loadResults} />
      <JobCard job={addJob} onCancelled={loadResults} />
      <JobCard job={zoteroJob} onCancelled={() => setZoteroJobId(null)} />

      {uploadMsg && <div className="rounded-lg bg-iris-500/10 px-3 py-2 text-sm text-iris-300">{uploadMsg}</div>}

      <AddPaperModal
        open={addOpen}
        onClose={closeAdd}
        url={addUrl}
        setUrl={setAddUrl}
        title={addTitle}
        setTitle={setAddTitle}
        busy={addBusy}
        onAddUrl={addByUrl}
        fileRef={addFileRef}
        onPickFile={addByFile}
      />

      {/* View switcher + select-all */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented<Tab>
          value={tab}
          onChange={setTab}
          options={[
            { id: "papers", label: t("Papers", "论文"), icon: <FileText size={14} />, count: papers.length },
            { id: "trends", label: t("Trends", "趋势"), icon: <TrendingUp size={14} /> },
          ]}
        />
        {tab === "papers" && papers.length > 0 && (
          <button
            onClick={toggleAllPapers}
            title={t("Toggle all", "全选/取消全选")}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg border border-white/[0.06] bg-ink-900/40 px-2.5 py-1.5 text-xs text-mist-300 transition hover:text-mist-100"
          >
            {allVisibleSelected ? (
              <CheckSquare size={14} className="text-iris-300" />
            ) : selectedVisible > 0 ? (
              <MinusSquare size={14} className="text-iris-300" />
            ) : (
              <Square size={14} />
            )}
            <span className="font-mono text-mist-500">
              {t(`${selectedVisible} of ${visiblePapers.length}`, `${selectedVisible} / ${visiblePapers.length}`)}
            </span>{" "}
            {t("selected for Zotero", "已选中用于 Zotero")}
          </button>
        )}
        {tab === "papers" && selectedPapers.size > 0 && (
          <button
            onClick={removeSelectedPapers}
            title={t("Delete all selected papers from this project", "从该项目中删除所有选中的论文")}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-300 transition hover:bg-rose-500/20 hover:text-rose-200"
          >
            <Trash2 size={14} /> {t(`Delete ${selectedPapers.size} selected`, `删除选中的 ${selectedPapers.size} 篇`)}
          </button>
        )}
        {tab === "papers" && selectedPapers.size > 0 && summaryPromptCustom && (
          <button
            onClick={() => void startBulkResummarize("full_text")}
            disabled={bulkResumRunning}
            title={t(
              "Regenerate the full-text Summary of the selected papers with this project's current Summary prompt",
              "使用该项目当前的总结提示词，重新生成选中论文的全文总结",
            )}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg border border-white/[0.08] bg-ink-900/40 px-2.5 py-1.5 text-xs text-mist-300 transition hover:text-mist-100 disabled:opacity-50"
          >
            <RefreshCw size={14} className={bulkResumRunning && bulkResumMode === "full_text" ? "animate-spin" : ""} />
            {t("Re-summarize Selected Full Text", "重新总结选中论文全文")}
          </button>
        )}
        {tab === "papers" && selectedPapers.size > 0 && codePromptCustom && (
          <button
            onClick={() => void startBulkResummarize("code")}
            disabled={bulkResumRunning}
            title={t(
              "Regenerate the code-repository analysis of the selected papers with this project's current Code-analysis prompt",
              "使用该项目当前的代码分析提示词，重新生成选中论文的代码仓库分析",
            )}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg border border-white/[0.08] bg-ink-900/40 px-2.5 py-1.5 text-xs text-mist-300 transition hover:text-mist-100 disabled:opacity-50"
          >
            <RefreshCw size={14} className={bulkResumRunning && bulkResumMode === "code" ? "animate-spin" : ""} />
            {t("Re-summarize Selected Codebase", "重新总结选中论文代码库")}
          </button>
        )}
      </div>

      {/* Title search over every explored paper, regardless of the chips below. */}
      {tab === "papers" && papers.length > 0 && (
        <div className="relative max-w-md">
          <Search
            size={14}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist-500"
          />
          <input
            className="input pl-9 pr-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("Search all explored papers by title…", "按标题搜索所有已探索的论文…")}
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              title={t("Clear search", "清除搜索")}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-mist-500 transition hover:text-mist-200"
            >
              <X size={14} />
            </button>
          )}
        </div>
      )}
      {tab === "papers" && searchQuery && (
        <div className="text-xs text-mist-500">
          {t(
            `${visiblePapers.length} of ${papers.length} papers match`,
            `${papers.length} 篇论文中有 ${visiblePapers.length} 篇匹配`,
          )}
        </div>
      )}

      {/* Filter papers by discovery day, plus a source chip for the AI Paper Finder
         (finder papers appear ONLY under their chip — day chips exclude them).
         With a single discovery day and no finder papers the per-day chip is redundant
         with "All", so the date is shown inline in the label instead. */}
      {tab === "papers" && !searchQuery && (dateGroups.length >= 1 || aipfCount > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-xs text-mist-500">
            <Calendar size={13} /> {t("Discovered", "发现于")}
            {dateGroups.length === 1 && aipfCount === 0 && (
              <span className="text-mist-300">{dayLabel(dateGroups[0][0])}</span>
            )}
          </span>
          <DateChip active={dateFilter === "all"} onClick={() => setDateFilter("all")} label={t("All", "全部")} count={papers.length} />
          {aipfCount > 0 && (
            <DateChip
              active={dateFilter === "aipf"}
              onClick={() => setDateFilter("aipf")}
              label="AI Paper Finder"
              count={aipfCount}
            />
          )}
          {(dateGroups.length > 1 || (dateGroups.length === 1 && aipfCount > 0)) &&
            dateGroups.map(([k, n]) => (
              <DateChip
                key={k}
                active={dateFilter === k}
                onClick={() => setDateFilter(k)}
                label={dayLabel(k)}
                count={n}
              />
            ))}
        </div>
      )}

      {tab === "papers" && (
        <PapersList
          pid={pid}
          papers={visiblePapers}
          selected={selectedPapers}
          onToggle={togglePaper}
          onDelete={removePaper}
          summaryPromptCustom={summaryPromptCustom}
          codePromptCustom={codePromptCustom}
          summaryReloadKey={summaryReloadKey}
          reload={loadResults}
        />
      )}
      {tab === "trends" && <TrendsView trends={trends} pid={pid} />}
    </div>
  );
}

// Live progress card for a running/finished job (discovery or add-paper).
function JobCard({ job, onCancelled }: { job: Job | null; onCancelled?: () => void }) {
  const { t } = useLang();
  const [canceling, setCanceling] = useState(false);
  if (!job) return null;
  const running = job.status === "running" || job.status === "queued";
  const stopping = canceling || !!job.cancel_requested; // requested but not yet honored
  const cancel = async () => {
    setCanceling(true);
    try {
      await api.cancelJob(job.id);
      onCancelled?.(); // the poller also reflects "canceled" on its own next tick
    } catch {
      /* poller will still surface the server state */
    } finally {
      setCanceling(false);
    }
  };
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="flex items-center gap-2 text-mist-300">
          <RefreshCw size={14} className={running ? "animate-spin" : ""} /> {t("Job", "任务")} #{job.id}
        </span>
        <div className="flex items-center gap-2">
          {running && (
            <button
              className="inline-flex items-center gap-1 rounded-md border border-white/[0.06] px-2 py-1 text-xs text-mist-400 transition hover:text-rose-300 disabled:opacity-50"
              onClick={cancel}
              disabled={stopping}
              title={t("Stop this job", "停止此任务")}
            >
              {stopping ? <Spinner /> : <Square size={12} />} {stopping ? t("Stopping…", "正在停止……") : t("Stop", "停止")}
            </button>
          )}
          <StatusPill status={job.status} />
        </div>
      </div>
      <ProgressBar value={job.progress} />
      {job.log && (
        <pre className="mt-3 max-h-32 overflow-y-auto rounded-lg bg-ink-950/70 p-3 font-mono text-xs text-mist-500">
          {job.log.trim()}
        </pre>
      )}
      {job.error && <div className="mt-2 text-sm text-rose-300">{job.error}</div>}
    </div>
  );
}

// Add a single paper by arXiv link/ID or by uploading a PDF.
function AddPaperModal({
  open,
  onClose,
  url,
  setUrl,
  title,
  setTitle,
  busy,
  onAddUrl,
  fileRef,
  onPickFile,
}: {
  open: boolean;
  onClose: () => void;
  url: string;
  setUrl: (v: string) => void;
  title: string;
  setTitle: (v: string) => void;
  busy: boolean;
  onAddUrl: () => void;
  fileRef: React.MutableRefObject<HTMLInputElement | null>;
  onPickFile: (f: File) => void;
}) {
  const { t } = useLang();
  return (
    <Modal open={open} onClose={onClose} maxWidth={460}>
      <h2 className="font-display text-base font-semibold text-white">{t("Add a paper", "添加论文")}</h2>
      <p className="mt-1 text-sm text-mist-500">
        {t(
          "Paste an arXiv link or ID, or upload a PDF — it’s summarized and shown like a discovered paper.",
          "粘贴 arXiv 链接或 ID，或上传 PDF——系统会对其进行总结，并像已发现的论文一样展示。",
        )}
      </p>

      <div className="mt-4">
        <label className="mb-1 block text-xs font-medium text-mist-400">{t("arXiv link or ID", "arXiv 链接或 ID")}</label>
        <div className="flex items-center gap-2">
          <input
            className="input flex-1"
            placeholder="https://arxiv.org/abs/2401.01234 or 2401.01234"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim() && !busy) onAddUrl();
            }}
          />
          <button className="btn-primary" onClick={onAddUrl} disabled={busy || !url.trim()}>
            {busy ? <Spinner /> : null} {t("Add", "添加")}
          </button>
        </div>
      </div>

      <div className="my-4 flex items-center gap-3 text-[11px] uppercase tracking-wider text-mist-600">
        <span className="h-px flex-1 bg-white/[0.06]" /> {t("or", "或")} <span className="h-px flex-1 bg-white/[0.06]" />
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-mist-400">{t("Upload a PDF", "上传 PDF")}</label>
        <input
          className="input mb-2 w-full"
          placeholder={t("Optional title (auto-detected if blank)", "可选标题（留空则自动识别）")}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button className="btn-subtle w-full" onClick={() => fileRef.current?.click()} disabled={busy}>
          {busy ? <Spinner /> : <Upload size={15} />} {t("Choose PDF…", "选择 PDF……")}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onPickFile(f);
            e.target.value = "";
          }}
        />
      </div>
    </Modal>
  );
}

// One pill in the discovery-date filter bar.
function DateChip({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 py-1 text-xs transition " +
        (active
          ? "border-iris-400/40 bg-iris-500/10 text-iris-200"
          : "border-white/[0.06] bg-ink-900/40 text-mist-400 hover:text-mist-100")
      }
    >
      {label}
      <span className="font-mono text-[11px] text-mist-500">{count}</span>
    </button>
  );
}

function PapersList({
  pid,
  papers,
  selected,
  onToggle,
  onDelete,
  summaryPromptCustom,
  codePromptCustom,
  summaryReloadKey,
  reload,
}: {
  pid: number;
  papers: Paper[];
  selected: Set<number>;
  onToggle: (id: number) => void;
  onDelete: (id: number) => void;
  summaryPromptCustom: boolean;
  codePromptCustom: boolean;
  summaryReloadKey: number;
  reload: () => void | Promise<void>;
}) {
  const { t } = useLang();
  if (papers.length === 0)
    return (
      <p className="py-10 text-center text-sm text-mist-500">
        {t("No papers yet. Run discovery to fetch them.", "尚无论文。运行发现以获取论文。")}
      </p>
    );
  return (
    <div className="space-y-3">
      {papers.map((p) => (
        <PaperCard
          key={p.id}
          pid={pid}
          p={p}
          selected={selected.has(p.id)}
          onToggle={() => onToggle(p.id)}
          onDelete={() => onDelete(p.id)}
          summaryPromptCustom={summaryPromptCustom}
          codePromptCustom={codePromptCustom}
          summaryReloadKey={summaryReloadKey}
          reload={reload}
        />
      ))}
    </div>
  );
}

function PaperCard({
  pid,
  p,
  selected,
  onToggle,
  onDelete,
  summaryPromptCustom,
  codePromptCustom,
  summaryReloadKey,
  reload,
}: {
  pid: number;
  p: Paper;
  selected: boolean;
  onToggle: () => void;
  onDelete: () => void;
  summaryPromptCustom: boolean;
  codePromptCustom: boolean;
  summaryReloadKey: number;
  reload: () => void | Promise<void>;
}) {
  const { t } = useLang();
  const pct = Math.round(p.relevance * 100);
  const published = p.published ? p.published.slice(0, 10) : "";
  const srcLabel = sourceLabel(p.source, t);
  // Conference label for AI-Paper-Finder papers, e.g. "CVPR 2026" (venue + year).
  const confYear = p.venue ? (p.published?.match(/(?:19|20)\d{2}/)?.[0] ?? "") : "";
  const conference = p.venue ? `${p.venue}${confYear ? ` ${confYear}` : ""}` : "";
  const [showSummary, setShowSummary] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [summary, setSummary] = useState("");
  const [codeUrl, setCodeUrl] = useState("");
  const [codeSummary, setCodeSummary] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  // Bilingual digest summary: toggle EN ⇄ 中文 (both are generated + stored).
  const [lang, setLang] = useState<"en" | "zh">("en");
  const summaryText = (lang === "zh" ? p.summary_zh : p.summary_en) || p.summary_en || p.summary_zh;
  const hasBilingual = !!(p.summary_en && p.summary_zh);
  const hasCode = p.code_status === "ok";

  // Lazy-load the (multi-KB) Summary + code analysis once, on first expand of either
  // panel; they then render in independent, non-interacting sections.
  const fetchSummary = async () => {
    const r = await api.paperSummary(pid, p.id);
    setSummary(r.summary_5pt || t("(no summary)", "（暂无总结）"));
    setCodeUrl(r.code_url || "");
    setCodeSummary(r.code_summary || "");
    setLoaded(true);
  };
  const ensureLoaded = async () => {
    if (loaded || loading) return;
    setLoading(true);
    try {
      await fetchSummary();
    } catch {
      setSummary(t("(failed to load summary)", "（总结加载失败）"));
    } finally {
      setLoading(false);
    }
  };
  const toggleSummary = () => {
    const next = !showSummary;
    setShowSummary(next);
    if (next) void ensureLoaded();
  };
  const toggleCode = () => {
    const next = !showCode;
    setShowCode(next);
    if (next) void ensureLoaded();
  };

  // Re-summarize / re-analyze with the project's CURRENT prompt (prompt debugging): enqueue
  // a job, poll it, then re-fetch the (now regenerated) summary into the open panel.
  const [resumJobId, setResumJobId] = useState<number | null>(null);
  const [resumKind, setResumKind] = useState<"summary" | "code" | "reparse" | null>(null);
  const resumJob = useJobPolling(resumJobId, () => {
    void fetchSummary().catch(() => {});
    // A reparse can flip has_fulltext (abstract → full text), so reload the list.
    if (resumKind === "reparse") void reload();
    setResumJobId(null);
  });
  const resumRunning =
    resumJobId != null && (!resumJob || resumJob.status === "queued" || resumJob.status === "running");
  const startResummarize = async (kind: "summary" | "code") => {
    if (resumRunning) return;
    setResumKind(kind);
    if (kind === "summary") setShowSummary(true);
    else setShowCode(true);
    try {
      const j = kind === "summary" ? await api.resummarizePaper(pid, p.id) : await api.reanalyzeCode(pid, p.id);
      setResumJobId(j.id);
    } catch {
      setResumKind(null);
    }
  };
  // Auto-recovery: a paper stuck on the abstract fallback re-parses itself the first
  // time its summary is opened — a fresh MinerU parse with the automatic arXiv-by-title
  // fallback, then re-summarize. No button; if it still can't be recovered (not on
  // arXiv), the manual "Upload PDF" appears. Once a forced re-parse has already proven
  // the full text unrecoverable (fulltext_recoverable === false), we stop auto-retrying
  // — the URL is unfetchable, so another attempt would only waste a fetch.
  const reparse = async () => {
    if (resumRunning) return;
    setResumKind("reparse");
    try {
      setResumJobId((await api.reparsePaper(pid, p.id)).id);
    } catch {
      setResumKind(null);
    }
  };
  const autoReparsedRef = useRef(false);
  useEffect(() => {
    if (
      showSummary && p.has_fulltext === false && p.fulltext_recoverable !== false
      && !autoReparsedRef.current && !resumRunning
    ) {
      autoReparsedRef.current = true;  // fire once per card
      void reparse();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSummary, p.has_fulltext]);
  // Last resort: upload the PDF yourself (its URL can't be fetched and it's not on arXiv).
  const pdfInputRef = useRef<HTMLInputElement | null>(null);
  const onUploadPdf = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";  // allow re-picking the same file
    if (!file || resumRunning) return;
    setResumKind("reparse");
    setShowSummary(true);
    try {
      setResumJobId((await api.uploadPaperPdf(pid, p.id, file)).id);
    } catch (err) {
      setResumKind(null);
      alert(err instanceof ApiError ? err.message : t("Upload failed.", "上传失败。"));
    }
  };
  // Manual code analysis: the user supplies a GitHub/GitLab URL (the detector missed the
  // repo, or it changed since discovery). Runs the same analysis, stored on the paper.
  const [showRepoForm, setShowRepoForm] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [repoErr, setRepoErr] = useState("");
  const analyzeRepo = async () => {
    if (resumRunning) return;
    if (!repoUrl.trim()) {
      setRepoErr(t("Enter a repository URL.", "请输入仓库链接。"));
      return;
    }
    setRepoErr("");
    setResumKind("code");
    setShowCode(true);
    try {
      const j = await api.reanalyzeCode(pid, p.id, repoUrl.trim());
      setResumJobId(j.id);
      setShowRepoForm(false);
      setRepoUrl("");
    } catch (e) {
      setResumKind(null);
      setRepoErr(e instanceof ApiError ? e.message : t("Analysis failed.", "分析失败。"));
    }
  };
  // After a bulk "Re-summarize Selected …" completes, refresh this card's already-loaded
  // summary so the new result shows without re-opening the panel.
  useEffect(() => {
    if (loaded) void fetchSummary().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaryReloadKey]);
  return (
    <div className={`card p-4 transition ${selected ? "border-iris-400/40" : ""}`}>
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 shrink-0 accent-iris-400"
          checked={selected}
          onChange={onToggle}
          aria-label={t(`Select ${p.title}`, `选择 ${p.title}`)}
        />
        <Ring value={p.relevance} tone="cyan" label={`${pct}%`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <h3 className="min-w-0 flex-1 font-medium leading-snug text-white">{p.title}</h3>
            <button
              onClick={() => {
                if (confirm(t(`Remove "${p.title.slice(0, 80)}" from this project?`, `从该项目中移除“${p.title.slice(0, 80)}”？`))) onDelete();
              }}
              title={t("Delete paper", "删除论文")}
              aria-label={t("Delete paper", "删除论文")}
              className="shrink-0 rounded-md p-1 text-mist-500 transition hover:bg-rose-500/10 hover:text-rose-300"
            >
              <Trash2 size={15} />
            </button>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist-500">
            {conference && (
              <span className="chip border-iris-400/40 bg-iris-500/10 text-iris-200">{conference}</span>
            )}
            {p.source === "ai_paper_finder" && (p.finder_score ?? 0) > 0 && (
              <span
                className="chip border-emerald-400/40 bg-emerald-500/10 text-emerald-200"
                title={t(
                  "AI Paper Finder semantic similarity (cosine) — the score the relevance threshold filtered on",
                  "AI Paper Finder 语义相似度（余弦）——相关性阈值据此进行筛选",
                )}
              >
                {t("semantic", "语义")} {p.finder_score!.toFixed(2)}
              </span>
            )}
            {srcLabel && (
              <span className="chip border-cyan-500/30 bg-cyan-500/10 text-cyan-300">{srcLabel}</span>
            )}
            <span>
              {p.authors.slice(0, 4).join(", ")}
              {p.authors.length > 4 ? t(" et al.", " 等") : ""}
              {!conference && published ? t(` · published ${published}`, ` · 发表于 ${published}`) : ""}
            </span>
            {p.created_at && (
              <span
                className="inline-flex items-center gap-1"
                title={t("When this paper was discovered (added to the project)", "该论文被发现（加入项目）的时间")}
              >
                <Clock size={11} /> {t("discovered", "发现于")} {fmtDateTime(p.created_at)}
              </span>
            )}
          </div>
          {summaryText && (
            <div className="mt-2">
              {hasBilingual && (
                <div className="mb-1 inline-flex overflow-hidden rounded-md border border-white/[0.08] text-[10px] font-medium">
                  {(["en", "zh"] as const).map((l) => (
                    <button
                      key={l}
                      onClick={() => setLang(l)}
                      className={`px-2 py-0.5 transition ${
                        lang === l ? "bg-iris-500/20 text-white" : "text-mist-500 hover:text-mist-200"
                      }`}
                    >
                      {l === "en" ? "EN" : "中文"}
                    </button>
                  ))}
                </div>
              )}
              <Markdown className="break-words text-sm leading-relaxed text-mist-300">
                {summaryText}
              </Markdown>
            </div>
          )}
          {p.document_id && (
            <div className="mt-2 space-y-1.5">
              {/* The Code Analysis button is always available (a missed/updated repo can
                  be analyzed on demand by URL); the prompt-debug re-run buttons below appear
                  only when the relevant prompt is customized (a default prompt would just
                  reproduce the shared default), and the codebase one only when a repo exists. */}
              <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => { setShowRepoForm((v) => !v); setRepoErr(""); }}
                    disabled={resumRunning}
                    title={t(
                      "Analyze a code repository for this paper by GitHub/GitLab URL",
                      "通过 GitHub/GitLab 链接分析该论文的代码仓库",
                    )}
                    className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-ink-900/40 px-2 py-1 text-[11px] text-mist-300 transition hover:text-mist-100 disabled:opacity-50"
                  >
                    <Code size={11} /> {hasCode ? t("Re-analyze repo (URL)", "重新分析仓库（链接）") : t("Code Analysis", "代码分析")}
                  </button>
                  {/* Only the abstract was parsed. Recovery runs automatically on open
                      (MinerU retry + arXiv-by-title fallback); this manual upload is the
                      last resort for papers whose PDF can't be fetched and aren't on arXiv. */}
                  {p.has_fulltext === false && !resumRunning && (
                    <>
                      <button
                        onClick={() => pdfInputRef.current?.click()}
                        title={t(
                          "Only the abstract could be parsed and this paper isn't on arXiv — upload the PDF yourself",
                          "当前仅能解析出摘要且该论文不在 arXiv 上——可自行上传 PDF",
                        )}
                        className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200 transition hover:text-amber-100"
                      >
                        <Upload size={11} /> {t("Upload PDF (abstract only)", "上传 PDF（仅有摘要）")}
                      </button>
                      <input ref={pdfInputRef} type="file" accept="application/pdf,.pdf"
                        className="hidden" onChange={(e) => void onUploadPdf(e)} />
                    </>
                  )}
                  {summaryPromptCustom && (
                    <button
                      onClick={() => void startResummarize("summary")}
                      disabled={resumRunning}
                      title={t(
                        "Re-run the full-text Summary with this project's current (custom) Summary prompt",
                        "使用该项目当前（自定义）的总结提示词，重新生成全文总结",
                      )}
                      className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-ink-900/40 px-2 py-1 text-[11px] text-mist-300 transition hover:text-mist-100 disabled:opacity-50"
                    >
                      <RefreshCw size={11} className={resumRunning && resumKind === "summary" ? "animate-spin" : ""} />
                      {t("Re-summarize Full Text", "重新总结全文")}
                    </button>
                  )}
                  {codePromptCustom && hasCode && (
                    <button
                      onClick={() => void startResummarize("code")}
                      disabled={resumRunning}
                      title={t(
                        "Re-run the code-repository analysis with this project's current (custom) Code-analysis prompt",
                        "使用该项目当前（自定义）的代码分析提示词，重新生成代码仓库分析",
                      )}
                      className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-ink-900/40 px-2 py-1 text-[11px] text-mist-300 transition hover:text-mist-100 disabled:opacity-50"
                    >
                      <RefreshCw size={11} className={resumRunning && resumKind === "code" ? "animate-spin" : ""} />
                      {t("Re-summarize Codebase", "重新总结代码库")}
                    </button>
                  )}
              </div>
              {/* Live log for re-summarize / re-analyze / re-parse actions. */}
              {(resumRunning || resumJob?.status === "failed") && <JobLog job={resumJob} />}
              {showRepoForm && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.06] bg-ink-950/40 p-2">
                  <input
                    className="input h-8 flex-1 text-xs"
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                    placeholder="https://github.com/owner/repo"
                    onKeyDown={(e) => { if (e.key === "Enter") void analyzeRepo(); }}
                  />
                  <button className="btn-primary h-8 px-3 text-xs" onClick={() => void analyzeRepo()} disabled={resumRunning}>
                    {resumRunning && resumKind === "code" ? <Spinner /> : <Code size={12} />} {t("Analyze", "分析")}
                  </button>
                  {repoErr && <span className="w-full text-[11px] text-rose-300">{repoErr}</span>}
                </div>
              )}
              {/* Paper summary — independent of the code analysis below. */}
              <div>
                <button
                  onClick={toggleSummary}
                  className="inline-flex items-center gap-1 text-xs text-iris-300 transition hover:text-iris-400"
                >
                  {showSummary ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {t("Summary", "总结")}
                </button>
                {showSummary && (
                  <div className="mt-1.5 rounded-lg border border-white/[0.06] bg-ink-950/50 p-3">
                    {loading && !loaded ? (
                      <span className="text-xs text-mist-300">{t("Loading…", "加载中……")}</span>
                    ) : (
                      <Markdown>{summary}</Markdown>
                    )}
                  </div>
                )}
              </div>
              {/* Code-repository analysis — separate output, shown only when analyzed. */}
              {hasCode && (
                <div>
                  <button
                    onClick={toggleCode}
                    className="inline-flex items-center gap-1 text-xs text-cyan-300 transition hover:text-cyan-200"
                  >
                    {showCode ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    <Code size={13} /> {t("Code repository analysis", "代码仓库分析")}
                  </button>
                  {showCode && (
                    <div className="mt-1.5 rounded-lg border border-white/[0.06] bg-ink-950/50 p-3">
                      {loading && !loaded ? (
                        <span className="text-xs text-mist-300">{t("Loading…", "加载中……")}</span>
                      ) : (
                        <>
                          {codeUrl && (
                            <a
                              href={codeUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mb-1.5 inline-flex items-center gap-1 font-mono text-[11px] text-iris-300 hover:underline"
                            >
                              <Github size={11} /> {codeUrl.replace(/^https?:\/\/(www\.)?/, "")}
                            </a>
                          )}
                          <Markdown>{codeSummary}</Markdown>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
              {/* Ask questions about this paper — grounded in its MinerU full text +
                  summary + code analysis. Only offered once real full text exists
                  (not the abstract-only fallback), else answers can't be grounded. */}
              {p.has_fulltext ? (
                <EntityChatPanel projectId={pid} scope="discovered" entityId={p.id} />
              ) : (
                <p className="text-[11px] text-mist-500">
                  {t(
                    "Chat is available once this paper's full text is parsed (MinerU/PDF).",
                    "解析出该论文全文（MinerU/PDF）后即可开启对话。",
                  )}
                </p>
              )}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {p.categories.slice(0, 3).map((c) => (
              <span key={c} className="chip">
                {c}
              </span>
            ))}
            {p.pdf_url && (
              <a
                href={p.pdf_url}
                target="_blank"
                rel="noreferrer"
                className="ml-auto inline-flex items-center gap-1 font-mono text-xs text-iris-300 hover:text-iris-400"
              >
                {isArxivId(p.arxiv_id) ? `arXiv:${p.arxiv_id}` : "PDF"} <ExternalLink size={12} />
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function TrendsView({ trends, pid }: { trends: Trends | null; pid: number }) {
  const { t } = useLang();
  if (!trends || trends.paper_count === 0)
    return <p className="py-10 text-center text-sm text-mist-500">{t("No trend data yet.", "尚无趋势数据。")}</p>;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="card p-4">
        <Eyebrow icon={<TrendingUp size={14} />}>{t("Top keywords", "热门关键词")}</Eyebrow>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {trends.top_keywords.slice(0, 24).map((k) => {
            const size = 0.75 + Math.min(1, k.weight / (trends.top_keywords[0]?.weight || 1)) * 0.6;
            return (
              <span
                key={k.term}
                className="font-display font-semibold text-iris-300"
                style={{ fontSize: `${size}rem` }}
              >
                {k.term}
              </span>
            );
          })}
        </div>
      </div>
      <div className="card overflow-hidden p-4">
        <div className="mb-3 text-sm font-medium text-white">{t("Word cloud", "词云")}</div>
        {trends.has_wordcloud ? (
          <img src={api.wordcloudUrl(pid)} alt={t("word cloud", "词云")} className="w-full rounded-lg" />
        ) : (
          <p className="text-sm text-mist-500">{t("No word cloud generated.", "尚未生成词云。")}</p>
        )}
      </div>
    </div>
  );
}

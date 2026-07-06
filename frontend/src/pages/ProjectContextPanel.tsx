import {
  BookMarked,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Download,
  FileText,
  RefreshCw,
  Telescope,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { SectionHeading } from "../components/layout/AppShell";
import { IconTile, PageLoader, Spinner } from "../components/ui";
import { api, type PaperDocument, type Project, type ProjectContext } from "../lib/api";
import { useLang } from "../lib/lang";
import { Markdown } from "../components/Markdown";

type Ctx = { project: Project };

// The auto-maintained context document, section by section.
const SECTIONS: { key: keyof ProjectContext; label: string; zh: string; icon: LucideIcon }[] = [
  { key: "background", label: "Background", zh: "背景", icon: BookOpen },
  { key: "papers_summary", label: "Papers", zh: "论文", icon: Telescope },
  { key: "references", label: "References", zh: "参考文献", icon: FileText },
];

function fmtDateTime(iso: string | null | undefined, never: string): string {
  if (!iso) return never;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return never;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ProjectContextPanel() {
  const { lang, t } = useLang();
  const { project } = useOutletContext<Ctx>();
  const pid = project.id;
  const [ctx, setCtx] = useState<ProjectContext | null>(null);
  const [papers, setPapers] = useState<PaperDocument[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setBusy(true);
    try {
      setErr(null);
      const [c, p] = await Promise.all([
        api.getContext(pid),
        api.exploredPapers(pid).catch(() => [] as PaperDocument[]),
      ]);
      setCtx(c);
      setPapers(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Failed to load context", "加载上下文失败"));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  if (!ctx)
    return err ? (
      <div className="card space-y-3 p-4">
        <p className="text-sm text-mist-300">{t("Couldn't load the context document:", "无法加载上下文文档：")} {err}</p>
        <button className="btn-ghost" onClick={load} disabled={busy}>
          {busy ? <Spinner /> : <RefreshCw size={15} />} {t("Retry", "重试")}
        </button>
      </div>
    ) : (
      <PageLoader />
    );

  return (
    <div className="animate-fade-up space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionHeading
          eyebrow={t("Project memory", "项目记忆")}
          title={t("Context document", "上下文文档")}
          desc={t("Auto-maintained after each step. It grounds the assistant and steers discovery summarization.", "在每个步骤后自动维护。它为助手提供依据，并引导文献总结。")}
        />
        <div className="flex shrink-0 items-center gap-2">
          <a className="btn-ghost" href={api.projectExportUrl(pid)} download
            title={t("Download the project's source & context files (.zip)", "下载项目的源文件与上下文文件（.zip）")}>
            <Download size={15} /> {t("Download all source files", "下载全部源文件")}
          </a>
          <button className="btn-ghost" onClick={load} disabled={busy}>
            {busy ? <Spinner /> : <RefreshCw size={15} />} {t("Refresh", "刷新")}
          </button>
        </div>
      </div>

      <div className="card flex flex-wrap items-center gap-x-4 gap-y-1 p-3 text-xs text-mist-500">
        <span>
          {t("Stage:", "阶段：")}{" "}
          <span className="chip border-iris-400/40 bg-iris-500/10 capitalize text-iris-300">
            {ctx.stage}
          </span>
        </span>
        <span>{t("Updated:", "更新于：")} {fmtDateTime(ctx.updated_at, t("never", "从未"))}</span>
      </div>

      <div className="space-y-3">
        {SECTIONS.map(({ key, label, zh, icon: Icon }) => {
          const value = (ctx[key] as string) || "";
          return (
            <div key={key} className="card p-4">
              <div className="mb-2 flex items-center gap-2">
                <IconTile icon={<Icon size={16} />} tone="iris" size="sm" />
                <div className="text-sm font-medium text-white">{lang === "zh" ? zh : label}</div>
              </div>
              {value.trim() ? (
                <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-mist-300">
                  {value.trim()}
                </pre>
              ) : (
                <p className="text-sm text-mist-600">{t("Not generated yet — run this stage to fill it.", "尚未生成——运行此阶段以填充内容。")}</p>
              )}
            </div>
          );
        })}
      </div>

      <ExploredPapers papers={papers} />
    </div>
  );
}

function ExploredPapers({ papers }: { papers: PaperDocument[] }) {
  const { t } = useLang();
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center gap-2">
        <IconTile icon={<BookMarked size={16} />} tone="cyan" size="sm" />
        <div className="text-sm font-medium text-white">
          {t("Explored papers", "已探索的论文")} <span className="text-mist-500">({papers.length})</span>
        </div>
      </div>
      <p className="mb-3 text-xs leading-relaxed text-mist-500">
        {t("Every explored paper, converted to markdown and summarized (5-point). These summaries ground the project assistant and are recorded as the project's references.", "每篇已探索的论文都会转换为 markdown 并进行五要点摘要。这些摘要为项目助手提供依据，并记录为项目的参考文献。")}
      </p>
      {papers.length === 0 ? (
        <p className="text-sm text-mist-600">
          {t("No papers explored yet — run discovery to convert and summarize them.", "尚未探索任何论文——运行发现以转换并摘要它们。")}
        </p>
      ) : (
        <div className="space-y-1.5">
          {papers.map((p) => (
            <PaperRow key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function PaperRow({ p }: { p: PaperDocument }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-white/[0.06] bg-ink-900/40 p-2">
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-2 text-left">
        {open ? (
          <ChevronDown size={14} className="mt-0.5 shrink-0 text-mist-500" />
        ) : (
          <ChevronRight size={14} className="mt-0.5 shrink-0 text-mist-500" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-mist-200">{p.title}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-mist-500">
            {p.source && <span className="chip">{p.source}</span>}
            {p.arxiv_id && <span className="font-mono">arXiv:{p.arxiv_id}</span>}
            {p.year && <span>{p.year}</span>}
            {p.extraction_method && <span className="text-mist-600">· {p.extraction_method}</span>}
          </span>
        </span>
      </button>
      {open && (
        <div className="mt-2 border-t border-white/[0.06] pt-2">
          <Markdown>{p.summary?.trim() || t("No summary yet.", "暂无摘要。")}</Markdown>
        </div>
      )}
    </div>
  );
}

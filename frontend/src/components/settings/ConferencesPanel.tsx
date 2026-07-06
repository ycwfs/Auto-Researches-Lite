import { Library, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { IconTile, Spinner } from "../ui";
import { api, type ConferenceIngestTask, type PaperfinderVenue } from "../../lib/api";
import { useLang } from "../../lib/lang";

/** Sources tab (part 2): the AI Paper Finder conference corpus — ingest / toggle / delete. */
export function ConferencesPanel() {
  const { t } = useLang();
  const [list, setList] = useState<PaperfinderVenue[] | null>(null);
  const [venue, setVenue] = useState("");
  const [year, setYear] = useState("");
  const [source, setSource] = useState<"openreview" | "cvf">("openreview");
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<ConferenceIngestTask | null>(null);
  const [err, setErr] = useState("");

  const load = () => api.adminConferences().then((r) => setList(r.venues)).catch(() => setList([]));
  useEffect(() => void load(), []);

  // Poll an in-flight ingest until it finishes, then refresh the list.
  useEffect(() => {
    if (!task || task.state === "done" || task.state === "failed") return;
    const id = setInterval(async () => {
      try {
        const s = await api.adminConferenceIngestStatus(task.task_id);
        setTask(s);
        if (s.state === "done" || s.state === "failed") void load();
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(id);
  }, [task?.task_id, task?.state]);

  const ingesting = !!task && task.state !== "done" && task.state !== "failed";

  const add = async () => {
    if (!venue.trim() || !year.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const tk = await api.adminAddConference(venue.trim(), year.trim(), source);
      setTask(tk);
      setVenue("");
      setYear("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Failed to start ingest", "启动导入失败"));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (v: PaperfinderVenue) => {
    setErr("");
    try {
      await api.adminToggleConference(v.venue, v.year ?? "", !(v.enabled ?? true));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Toggle failed", "切换失败"));
    }
    void load();
  };

  const remove = async (v: PaperfinderVenue) => {
    if (!window.confirm(t(`Delete all ${v.count} ${v.venue} ${v.year} papers from the corpus?`, `从语料库中删除全部 ${v.count} 篇 ${v.venue} ${v.year} 论文？`))) return;
    setErr("");
    try {
      await api.adminDeleteConference(v.venue, v.year ?? "");
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Delete failed", "删除失败"));
    }
    void load();
  };

  return (
    <div className="space-y-5">
      <div className="card p-5">
        <div className="mb-1 flex items-center gap-2.5">
          <IconTile icon={<Plus size={16} />} tone="iris" size="sm" />
          <h2 className="font-display text-base font-semibold text-white">{t("Add a conference", "添加会议")}</h2>
        </div>
        <p className="mb-3 text-sm text-mist-500">
          {t("Ingests a conference's accepted papers into the AI Paper Finder corpus (collect + embed; takes a few minutes). It then appears in every project's conference picker.", "将会议的录用论文导入 AI Paper Finder 语料库（采集 + 向量化；需要几分钟）。完成后会出现在每个项目的会议选择器中。")}
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <input className="input w-40" placeholder={t("Venue (e.g. CVPR)", "会议（例如 CVPR）")} value={venue}
            onChange={(e) => setVenue(e.target.value)} />
          <input className="input w-24" placeholder={t("Year", "年份")} value={year}
            onChange={(e) => setYear(e.target.value)} />
          <select className="input w-64" value={source} onChange={(e) => setSource(e.target.value as "openreview" | "cvf")}>
            <option value="openreview">OpenReview — ICLR / ICML / NeurIPS</option>
            <option value="cvf">CVF Open Access — CVPR / ICCV / WACV</option>
          </select>
          <button className="btn-primary" onClick={add} disabled={busy || ingesting || !venue.trim() || !year.trim()}>
            {busy || ingesting ? <Spinner /> : <Plus size={15} />} {t("Add", "添加")}
          </button>
        </div>
        {err && <p className="mt-2 text-sm text-rose-300">{err}</p>}
        {task && (
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-ink-900/40 p-3 text-sm">
            <span className="flex items-center gap-2 text-mist-300">
              {ingesting && <Spinner />}
              <span className="font-mono text-xs">
                {task.venue} {task.year} — {task.state}
                {task.state === "collecting" && t(` (collected ${task.collected ?? 0})`, `（已采集 ${task.collected ?? 0}）`)}
                {task.state === "embedding" && t(` (embedding ${task.collected ?? 0} papers…)`, `（正在向量化 ${task.collected ?? 0} 篇论文…）`)}
                {(task.state === "done" || task.state === "failed") && task.message ? ` — ${task.message}` : ""}
              </span>
            </span>
          </div>
        )}
      </div>

      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2.5">
          <IconTile icon={<Library size={16} />} tone="cyan" size="sm" />
          <h2 className="font-display text-base font-semibold text-white">{t("Conferences in the corpus", "语料库中的会议")}</h2>
        </div>
        {list === null ? (
          <Spinner />
        ) : list.length === 0 ? (
          <p className="text-sm text-mist-500">{t("No conferences yet — add one above.", "暂无会议 — 请在上方添加。")}</p>
        ) : (
          <div className="divide-y divide-white/[0.05]">
            {list.map((v) => {
              const on = v.enabled ?? true;
              return (
                <div key={`${v.venue} ${v.year}`} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <span className="font-medium text-mist-100">
                      {v.venue} {v.year}
                    </span>
                    <span className="ml-2 font-mono text-xs text-mist-500">{v.count} {t("papers", "篇论文")}</span>
                    {!on && (
                      <span className="ml-2 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300/80">
                        {t("disabled", "已禁用")}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 text-xs text-mist-400">
                      <input type="checkbox" checked={on} onChange={() => toggle(v)} /> {t("enabled", "已启用")}
                    </label>
                    <button
                      className="text-mist-500 transition hover:text-rose-300"
                      onClick={() => remove(v)}
                      title={t("Delete this conference-year from the corpus", "从语料库中删除该会议年份")}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

import { RefreshCw, Square } from "lucide-react";
import { api, type Job } from "../lib/api";
import { useLang } from "../lib/lang";
import { ProgressBar, Spinner } from "./ui";

/**
 * Live job panel: the running step (last log line) + progress bar + the full
 * scrollable log + a Stop button while active, and the error when it fails.
 * Reused by every API-backed action so their logs are shown consistently
 * (paper summary / code analysis / re-parse…).
 */
export function JobLog({
  job,
  title,
  canStop = true,
}: {
  job: Job | null;
  title?: string; // optional prefix on the running-step line (e.g. "overview figure")
  canStop?: boolean;
}) {
  const { t } = useLang();
  if (!job) return null;
  const active = job.status === "running" || job.status === "queued";
  const log = (job.log || "").trim();
  const lastLine = log.split("\n").slice(-1)[0] || t("working…", "处理中…");
  return (
    <div className="mt-3 rounded-lg border border-white/[0.06] bg-ink-900/40 p-3">
      <div className="mb-2 flex items-center justify-between gap-2 text-[13px] text-mist-300">
        <span className="flex min-w-0 items-center gap-2">
          {active && <RefreshCw size={14} className="shrink-0 animate-spin" />}
          <span className="truncate font-mono text-xs">
            {title ? `${title}: ` : ""}
            {lastLine}
          </span>
        </span>
        {canStop && active && (
          <button
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/[0.06] px-2 py-1 text-xs text-mist-400 transition hover:text-rose-300 disabled:opacity-50"
            onClick={() => void api.cancelJob(job.id).catch(() => {})}
            disabled={!!job.cancel_requested}
            title={t("Stop this job", "停止此任务")}
          >
            {job.cancel_requested ? <Spinner /> : <Square size={12} />}{" "}
            {job.cancel_requested ? t("Stopping…", "停止中…") : t("Stop", "停止")}
          </button>
        )}
      </div>
      {active && <ProgressBar value={job.progress} />}
      {log && (
        <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-mist-500">
          {log}
        </pre>
      )}
      {job.status === "failed" && (
        <div className="mt-1 text-xs text-rose-300">{job.error || t("Failed.", "失败。")}</div>
      )}
    </div>
  );
}

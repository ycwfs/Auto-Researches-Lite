import { Cpu } from "lucide-react";
import { useEffect, useState } from "react";
import { IconTile, Spinner } from "../ui";
import { api, type WorkersState } from "../../lib/api";
import { useLang } from "../../lib/lang";

/** Runtime tab: background-worker concurrency & health. */
export function RuntimePanel() {
  return (
    <div className="space-y-5">
      <WorkersCard />
    </div>
  );
}

function WorkersCard() {
  const { t } = useLang();
  const [st, setSt] = useState<WorkersState | null>(null);
  const [val, setVal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.adminWorkers().then((s) => { setSt(s); setVal(s.stored); }).catch(() => {});
    const id = setInterval(() => api.adminWorkers().then(setSt).catch(() => {}), 5000);
    return () => clearInterval(id);
  }, []);

  const save = async () => {
    setBusy(true);
    setErr("");
    setSaved(false);
    try {
      const s = await api.adminSetWorkers(val);
      setSt(s);
      setSaved(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Save failed", "保存失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card p-6">
      <div className="mb-1 flex items-center gap-2">
        <IconTile icon={<Cpu size={16} />} tone="iris" size="sm" />
        <div className="font-display text-sm font-semibold text-white">{t("Background workers", "后台 worker")}</div>
      </div>
      <p className="mb-4 text-xs leading-relaxed text-mist-500">
        {t("How many discovery / AI Paper Finder jobs run at once, per worker container. Applied", "每个 worker 容器同时运行多少个 发现 / AI Paper Finder 任务。")} <b>{t("live", "实时生效")}</b> — {t("growing spawns workers instantly; shrinking lets the extras finish their current job, then stop. No restart.", "调大会立即启动 worker；调小则让多余 worker 完成当前任务后停止。无需重启。")} <span className="font-mono">0</span> = {t("use the server default", "使用服务器默认值")}（{st?.env_default ?? "?"}）。
      </p>
      <div className="flex flex-wrap items-end gap-4">
        <label className="text-sm">
          <span className="label">{t("Concurrency per container", "每个容器的并发数")}</span>
          <input
            className="input mt-1 w-40"
            type="number"
            min={0}
            max={64}
            value={val}
            onChange={(e) => { setVal(Number(e.target.value)); setSaved(false); }}
          />
        </label>
        <button className="btn-primary" onClick={save} disabled={busy || st == null}>
          {busy && <Spinner />} {t("Apply", "应用")}
        </button>
        {saved && <span className="text-sm text-emerald-300">{t("✓ Applied (workers converge within ~12s)", "✓ 已应用（worker 约 12 秒内收敛）")}</span>}
        {err && <span className="text-sm text-rose-300">{err}</span>}
      </div>
      {st && (
        <div className="mt-3 flex flex-wrap gap-5 text-xs text-mist-400">
          <span>{t("Effective target:", "生效目标值：")} <b className="text-mist-200">{st.target}</b> / {t("container", "容器")}</span>
          <span title={t("Workers with a recent heartbeat, across all worker containers. A worker that dies ungracefully clears within ~90s.", "所有 worker 容器中近期有心跳的 worker。异常退出的 worker 约 90 秒内清除。")}>{t("Live workers (all containers):", "在线 worker（全部容器）：")} <b className="text-mist-200">{st.live < 0 ? "—" : st.live}</b></span>
          <span className="text-mist-500">{t("Total parallel jobs ≈ target × worker replicas.", "并行任务总数 ≈ 目标值 × worker 副本数。")}</span>
        </div>
      )}
    </div>
  );
}

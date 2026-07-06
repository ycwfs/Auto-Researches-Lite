import { Plug, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { IconTile, PageLoader, Spinner } from "../ui";
import { api, type ApiTestResult, type IntegrationConfig } from "../../lib/api";
import { useLang } from "../../lib/lang";

/** Integrations tab: MinerU PDF → markdown extraction service. */
export function IntegrationsPanel() {
  const [cfg, setCfg] = useState<IntegrationConfig | null>(null);
  useEffect(() => {
    api.adminGetIntegrations().then(setCfg);
  }, []);
  if (!cfg) return <PageLoader />;
  return (
    <div className="grid gap-6">
      <MinerUCard cfg={cfg} onSaved={setCfg} />
    </div>
  );
}

/** Inline ✓/✗ result for a "Test connection" button. */
function TestResult({ test }: { test: ApiTestResult | "loading" | null }) {
  if (!test || test === "loading") return null;
  return (
    <span className={`text-sm ${test.ok ? "text-emerald-300" : "text-rose-300"}`}>
      {test.ok ? "✓ " : "✗ "}
      {test.detail}
    </span>
  );
}

function MinerUCard({ cfg, onSaved }: { cfg: IntegrationConfig; onSaved: (c: IntegrationConfig) => void }) {
  const { t } = useLang();
  const [url, setUrl] = useState(cfg.mineru_api_url);
  const [key, setKey] = useState("");
  const [maxWait, setMaxWait] = useState(cfg.mineru_max_wait_seconds ?? 0);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState<ApiTestResult | "loading" | null>(null);
  const [err, setErr] = useState("");

  const save = async () => {
    setBusy(true);
    setErr("");
    setSaved(false);
    try {
      const updated = await api.adminUpdateIntegrations({
        mineru_api_url: url,
        mineru_max_wait_seconds: maxWait,
        ...(key ? { mineru_api_key: key } : {}),
      });
      onSaved(updated);
      setUrl(updated.mineru_api_url);
      setMaxWait(updated.mineru_max_wait_seconds ?? 0);
      setKey("");
      setSaved(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Save failed", "保存失败"));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    setTest("loading");
    try {
      setTest(await api.adminTestMineru());
    } catch (e) {
      setTest({ ok: false, detail: e instanceof Error ? e.message : t("Test failed", "测试失败") });
    }
  };

  return (
    <div className="card p-6">
      <div className="mb-4 flex items-center gap-2">
        <IconTile icon={<Plug size={16} />} tone="iris" size="sm" />
        <div>
          <div className="font-display text-sm font-semibold text-white">{t("MinerU (PDF → markdown)", "MinerU（PDF → markdown）")}</div>
          <div className="text-xs text-mist-500">
            {t("Converts paper PDFs to markdown for the 5-point summaries. Without it, extraction falls back to pypdf, then the abstract.", "将论文 PDF 转换为 markdown 以生成 5 点摘要。未配置时，提取会回退到 pypdf，再回退到摘要。")}
          </div>
        </div>
      </div>
      <div className="grid gap-4">
        <div>
          <label className="label">{t("MinerU API URL", "MinerU API URL")}</label>
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…/mineru/extract" />
        </div>
        <div>
          <label className="label">
            {t("MinerU API key", "MinerU API 密钥")} {cfg.mineru_key_set && <span className="text-emerald-300">{t("(set)", "（已设置）")}</span>}
          </label>
          <input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)}
            placeholder={cfg.mineru_key_set ? t("•••••• (leave blank to keep current)", "••••••（留空以保留当前值）") : t("paste the API key", "粘贴 API 密钥")} />
        </div>
        <div>
          <label className="label">{t("Max parse wait, seconds (0 = default 120)", "解析最长等待秒数（0 = 默认 120）")}</label>
          <input className="input w-40" type="number" min={0} max={3600} value={maxWait}
            onChange={(e) => setMaxWait(Math.max(0, Math.min(3600, Number(e.target.value) || 0)))} />
          <p className="mt-1.5 text-xs text-mist-500">
            {t(
              "How long to wait for a MinerU parse before falling back to pypdf/abstract. Raise it for slow/large PDFs (e.g. OpenReview) that were timing out and only got the abstract.",
              "在回退到 pypdf/摘要之前，等待 MinerU 解析的最长时间。对于较慢或较大的 PDF（如 OpenReview）此前因超时只取到摘要，可调高此值。",
            )}
          </p>
        </div>
      </div>
      {err && <div className="mt-3 text-sm text-rose-400">{err}</div>}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy}>
          {busy && <Spinner />} {t("Save", "保存")}
        </button>
        {saved && <span className="text-sm text-emerald-400">{t("Saved", "已保存")}</span>}
        <button className="btn-subtle" onClick={runTest} disabled={test === "loading"}>
          {test === "loading" ? <Spinner /> : <Zap size={14} />} {t("Test connection", "测试连接")}
        </button>
        <TestResult test={test} />
      </div>
    </div>
  );
}

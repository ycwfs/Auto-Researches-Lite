import { Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { IconTile, PageLoader, Spinner } from "../ui";
import { api, type AdminSource, type AdminSourceWrite } from "../../lib/api";
import { useLang } from "../../lib/lang";

/** Sources tab (part 1): public paper-search APIs — toggle, edit config, manage API-key pools. */
export function SourcesPanel() {
  const { t } = useLang();
  const [sources, setSources] = useState<AdminSource[] | null>(null);
  const [adding, setAdding] = useState(false);
  const load = () => api.adminSources().then(setSources);
  useEffect(() => void load(), []);
  if (!sources) return <PageLoader />;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="font-display text-base font-semibold text-white">{t("Paper sources", "论文源")}</h2>
      </div>
      <p className="text-sm text-mist-500">
        {t("Public paper-search APIs. Toggle, edit config, manage API keys (multiple keys are rotated to avoid rate limits), and add or remove sources.", "公开的论文检索 API。可启用/停用、编辑配置、管理 API 密钥（多个密钥会轮换以规避速率限制），并增删论文源。")}
      </p>
      <div className="flex justify-end">
        <button className="btn-subtle px-3 py-1.5 text-sm" onClick={() => setAdding(true)}>
          <Plus size={14} /> {t("Add source", "添加论文源")}
        </button>
      </div>
      {adding && (
        <SourceAddForm onClose={() => setAdding(false)} onCreated={() => { setAdding(false); void load(); }} />
      )}
      {sources.map((s) => (
        <SourceCard key={s.id} source={s} onChanged={load} />
      ))}
    </div>
  );
}

function SourceAddForm({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { t } = useLang();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async () => {
    if (!key.trim() || !name.trim()) return;
    setBusy(true);
    setErr("");
    try {
      await api.adminCreateSource({ key: key.trim(), name: name.trim(), description });
      onCreated();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Create failed", "创建失败"));
      setBusy(false);
    }
  };
  return (
    <div className="card space-y-2 p-4">
      <div className="grid grid-cols-2 gap-2">
        <input className="input" placeholder={t("key (e.g. my_source)", "key（例如 my_source）")} value={key} onChange={(e) => setKey(e.target.value)} />
        <input className="input" placeholder={t("Display name", "显示名称")} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <input className="input" placeholder={t("Description (optional)", "描述（可选）")} value={description} onChange={(e) => setDescription(e.target.value)} />
      {err && <p className="text-xs text-rose-300">{err}</p>}
      <div className="flex gap-2">
        <button className="btn-primary px-3 py-1.5 text-xs" onClick={submit} disabled={busy || !key.trim() || !name.trim()}>
          {busy && <Spinner />} {t("Create", "创建")}
        </button>
        <button className="btn-subtle px-3 py-1.5 text-xs" onClick={onClose}>{t("Cancel", "取消")}</button>
      </div>
    </div>
  );
}

function SourceCard({ source, onChanged }: { source: AdminSource; onChanged: () => void }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(source.name);
  const [description, setDescription] = useState(source.description);
  const [configText, setConfigText] = useState(JSON.stringify(source.config || {}, null, 2));
  const [keysText, setKeysText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    setName(source.name);
    setDescription(source.description);
    setConfigText(JSON.stringify(source.config || {}, null, 2));
  }, [source]);

  const save = async () => {
    setBusy(true);
    setErr("");
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(configText || "{}");
    } catch {
      setErr(t("Config is not valid JSON", "配置不是有效的 JSON"));
      setBusy(false);
      return;
    }
    const body: AdminSourceWrite = { name, description, config: config as Record<string, string> };
    const keys = keysText.split("\n").map((k) => k.trim()).filter(Boolean);
    if (keys.length) body.api_keys = keys; // entering keys REPLACES the pool
    try {
      await api.adminUpdateSource(source.id, body);
      setKeysText("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("Save failed", "保存失败"));
    } finally {
      setBusy(false);
    }
  };
  const clearKeys = async () => {
    setBusy(true);
    await api.adminUpdateSource(source.id, { api_keys: [] });
    setKeysText("");
    onChanged();
    setBusy(false);
  };
  const remove = async () => {
    if (!confirm(t(`Delete source "${source.name}"?`, `删除论文源「${source.name}」？`))) return;
    await api.adminDeleteSource(source.id);
    onChanged();
  };

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <IconTile icon={<ShieldCheck size={16} />} tone="cyan" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 font-medium text-white">
              {source.name}
              <span className="chip">{source.key}</span>
              {source.key_count > 0 && (
                <span className="chip border-cyan-500/30 bg-cyan-500/10 text-cyan-300">
                  {source.key_count} {t(`key${source.key_count > 1 ? "s" : ""}`, "个密钥")}
                </span>
              )}
            </div>
            <div className="mt-1 text-xs text-mist-500">{source.description}</div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-mist-300">
            <input
              type="checkbox"
              checked={source.enabled}
              onChange={async (e) => {
                await api.adminUpdateSource(source.id, { enabled: e.target.checked });
                onChanged();
              }}
            />
            {source.enabled ? t("Enabled", "已启用") : t("Disabled", "已禁用")}
          </label>
          <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setOpen(!open)}>
            {open ? t("Close", "收起") : t("Edit", "编辑")}
          </button>
          <button
            className="rounded-md p-1 text-mist-500 transition hover:bg-rose-500/10 hover:text-rose-300"
            title={t("Delete source", "删除论文源")}
            onClick={remove}
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      {open && (
        <div className="mt-3 space-y-2 border-t border-white/[0.06] pt-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">{t("Name", "名称")}</label>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label className="label">{t("Description", "描述")}</label>
              <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
          </div>
          <div>
            <label className="label">{t("Config (JSON)", "配置（JSON）")}</label>
            <textarea
              className="input min-h-[80px] resize-y font-mono text-xs"
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t(`API keys — one per line (${source.key_count} configured)`, `API 密钥 — 每行一个（已配置 ${source.key_count} 个）`)}</label>
            <textarea
              className="input min-h-[64px] resize-y font-mono text-xs"
              placeholder={t("Enter keys to REPLACE the pool; leave blank to keep the current keys", "输入密钥将「替换」整个密钥池；留空则保留当前密钥")}
              value={keysText}
              onChange={(e) => setKeysText(e.target.value)}
            />
            <p className="mt-1 text-[11px] text-mist-500">
              {t("Stored encrypted, never shown again. Multiple keys are rotated across requests to avoid per-key rate limits.", "加密存储，不再次显示。多个密钥会在请求间轮换，以规避单密钥的速率限制。")}
            </p>
          </div>
          {err && <p className="text-xs text-rose-300">{err}</p>}
          <div className="flex items-center gap-2">
            <button className="btn-primary px-3 py-1.5 text-xs" onClick={save} disabled={busy}>
              {busy && <Spinner />} {t("Save", "保存")}
            </button>
            {source.key_count > 0 && (
              <button className="btn-subtle px-3 py-1.5 text-xs" onClick={clearKeys} disabled={busy}>
                {t("Clear keys", "清除密钥")}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

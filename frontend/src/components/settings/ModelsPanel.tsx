import { Cpu, Pencil, Plus, Save, Trash2, X, Zap } from "lucide-react";
import { type Dispatch, type SetStateAction, useEffect, useState } from "react";
import { IconTile, PageLoader, Spinner } from "../ui";
import { api, type AdminModel, type ApiTestResult, type ModelKind } from "../../lib/api";
import { useLang } from "../../lib/lang";

const ALL_EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;

const EMPTY_MODEL = {
  label: "",
  kind: "api" as ModelKind,
  provider: "claude",
  api_style: "", // "" = infer from provider; else "anthropic" | "openai"
  base_url: "",
  model: "",
  api_key: "",
  enabled: true,
  // Effort levels this model accepts. Default to all (Claude models); uncheck the ones a
  // model doesn't support (e.g. xhigh for Opus 4.6), or clear it for no-effort models.
  supported_efforts: ["low", "medium", "high", "xhigh", "max"] as string[],
};
type ModelForm = typeof EMPTY_MODEL;

/** Models tab: the Channel-A (api) model catalog. */
export function ModelsPanel() {
  return (
    <div className="space-y-6">
      <ModelCatalog />
    </div>
  );
}

/** The shared field grid used by both the Add and inline-Edit model forms.
 * `keyPlaceholder` differs (Add: stored once; Edit: blank keeps the current key). */
function ModelFormFields({
  form,
  setForm,
  keyPlaceholder,
}: {
  form: ModelForm;
  setForm: Dispatch<SetStateAction<ModelForm>>;
  keyPlaceholder: string;
}) {
  const { t: tr } = useLang();
  const toggleEffort = (e: string) =>
    setForm((f) => ({
      ...f,
      supported_efforts: f.supported_efforts.includes(e)
        ? f.supported_efforts.filter((x) => x !== e)
        : [...f.supported_efforts, e],
    }));
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div>
        <label className="label">{tr("Label", "标签")}</label>
        <input className="input" value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
          placeholder={tr("e.g. Opus 4.8 (proxy)", "例如 Opus 4.8 (proxy)")} />
      </div>
      <div>
        <label className="label">{tr("Provider", "提供方")}</label>
        <input className="input" value={form.provider}
          onChange={(e) => setForm({ ...form, provider: e.target.value })}
          placeholder="claude / openai / deepseek / glm / minimax …" />
      </div>
      <div>
        <label className="label">{tr("API style", "API 协议")}</label>
        <select className="input" value={form.api_style}
          onChange={(e) => setForm({ ...form, api_style: e.target.value })}>
          <option value="">{tr("Auto (from provider)", "自动（按提供方推断）")}</option>
          <option value="anthropic">{tr("Anthropic (Messages API)", "Anthropic（Messages API）")}</option>
          <option value="openai">{tr("OpenAI (Chat Completions)", "OpenAI（Chat Completions）")}</option>
        </select>
      </div>
      <div>
        <label className="label">{tr("Model id", "模型 ID")}</label>
        <input className="input" value={form.model}
          onChange={(e) => setForm({ ...form, model: e.target.value })}
          placeholder={tr("e.g. claude-opus-4-8", "例如 claude-opus-4-8")} />
      </div>
      <div className="sm:col-span-2">
        <label className="label">{tr("Base URL (optional)", "Base URL（可选）")}</label>
        <input className="input" value={form.base_url}
          onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          placeholder="https://api.deepseek.com/anthropic" />
        <p className="mt-1 text-xs text-mist-500">
          {tr(
            "Provider is only a label — API style picks the client. Use OpenAI for OpenAI-compatible endpoints (deepseek / glm / minimax native), Anthropic for …/anthropic endpoints. Auto = OpenAI when provider is “openai”, else Anthropic. Use Test to confirm — a mismatch silently falls back to mock output.",
            "提供方仅作标签——API 协议决定使用哪个客户端。OpenAI 兼容端点（deepseek / glm / minimax 原生）选 OpenAI，…/anthropic 端点选 Anthropic。自动 = 提供方为“openai”时用 OpenAI，否则用 Anthropic。请用「测试」确认——不匹配会静默回退到 mock 输出。",
          )}
        </p>
      </div>
      <div className="sm:col-span-2">
        <label className="label">{tr("API key", "API 密钥")}</label>
        <input className="input" type="password" value={form.api_key}
          onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          placeholder={keyPlaceholder} />
      </div>
      <div className="sm:col-span-2">
        <label className="label">{tr("Supported effort levels", "支持的推理强度")}</label>
        <div className="flex flex-wrap gap-2">
          {ALL_EFFORTS.map((e) => (
            <button key={e} onClick={() => toggleEffort(e)}
              className={`chip ${form.supported_efforts.includes(e) ? "text-iris-300 ring-1 ring-iris-400/40" : ""}`}>
              {form.supported_efforts.includes(e) && "✓ "}{e}
            </button>
          ))}
        </div>
        <p className="mt-1 text-xs text-mist-500">
          {tr(
            "Only these are offered in the per-step picker (empty = no effort). A requested level clamps down to the highest supported one.",
            "仅这些会出现在分步选择器中（留空=不使用推理）。所选强度会向下取到不超过它的最高受支持等级。",
          )}
        </p>
      </div>
    </div>
  );
}

function ModelCatalog() {
  const { t: tr } = useLang();
  const [models, setModels] = useState<AdminModel[] | null>(null);
  const [form, setForm] = useState({ ...EMPTY_MODEL });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [tests, setTests] = useState<Record<number, ApiTestResult | "loading">>({});
  const load = () => api.adminModels().then(setModels);
  useEffect(() => void load(), []);

  const testModel = async (id: number) => {
    setTests((t) => ({ ...t, [id]: "loading" }));
    try {
      const r = await api.adminTestModel(id);
      setTests((t) => ({ ...t, [id]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [id]: { ok: false, detail: e instanceof Error ? e.message : tr("Test failed", "测试失败") } }));
    }
  };

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<ModelForm>({ ...EMPTY_MODEL });
  const [editBusy, setEditBusy] = useState(false);
  const [editErr, setEditErr] = useState("");

  const startEdit = (m: AdminModel) => {
    setEditErr("");
    setEditingId(m.id);
    setEditForm({
      label: m.label,
      kind: m.kind,
      provider: m.provider,
      api_style: m.api_style || "",
      base_url: m.base_url,
      model: m.model,
      api_key: "", // blank keeps the stored key
      enabled: m.enabled,
      supported_efforts: [...(m.supported_efforts || [])],
    });
  };

  const saveEdit = async () => {
    if (editingId == null) return;
    setEditErr("");
    const clean = {
      ...editForm,
      label: editForm.label.trim(),
      provider: editForm.provider.trim(),
      model: editForm.model.trim(),
      base_url: editForm.base_url.trim(),
      api_key: editForm.api_key.trim(),
    };
    if (!clean.label || !clean.model) {
      setEditErr(tr("Label and model id are required.", "标签和模型 ID 为必填项。"));
      return;
    }
    setEditBusy(true);
    try {
      await api.adminUpdateModel(editingId, clean); // blank api_key is ignored server-side
      setEditingId(null);
      void load();
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : tr("Failed to save model", "保存模型失败"));
    } finally {
      setEditBusy(false);
    }
  };

  const create = async () => {
    setErr("");
    const clean = {
      ...form,
      label: form.label.trim(),
      provider: form.provider.trim(),
      model: form.model.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
    };
    if (!clean.label || !clean.model) {
      setErr(tr("Label and model id are required.", "标签和模型 ID 为必填项。"));
      return;
    }
    setBusy(true);
    try {
      await api.adminCreateModel(clean);
      setForm({ ...EMPTY_MODEL });
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : tr("Failed to add model", "添加模型失败"));
    } finally {
      setBusy(false);
    }
  };

  if (!models) return <PageLoader />;
  return (
    <div className="space-y-4">
      {/* Existing models */}
      <div className="space-y-3">
        {models.length === 0 && (
          <div className="card p-4 text-sm text-mist-500">
            {tr("No models yet. Add one below — you'll then be able to pick it per step.", "暂无模型。在下方添加一个，即可在每个步骤中选用。")}
          </div>
        )}
        {models.map((m) => (
          <div key={m.id} className="card">
           <div className="flex flex-wrap items-center justify-between gap-3 p-4">
            <div className="flex min-w-0 items-start gap-3">
              <IconTile icon={<Cpu size={16} />} tone="iris" />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 font-medium text-white">
                  {m.label}
                  <span className="chip">{m.provider}</span>
                  {m.api_style && <span className="chip text-iris-300">{m.api_style}</span>}
                  <span className="chip">{m.model}</span>
                  {m.key_set ? (
                    <span className="chip text-emerald-300">{tr("key set", "已设密钥")}</span>
                  ) : (
                    <span className="chip text-amber-300">{tr("no key", "无密钥")}</span>
                  )}
                  {m.last_test_ok === true && (
                    <span className="chip text-emerald-300" title={m.last_test_at ?? undefined}>
                      {tr("API ✓", "API ✓")}
                    </span>
                  )}
                  {m.last_test_ok === false && (
                    <span className="chip text-rose-300" title={m.last_test_at ?? undefined}>
                      {tr("API ✗ · hidden from pickers", "API ✗ · 已从选择器隐藏")}
                    </span>
                  )}
                </div>
                <div className="mt-1 truncate font-mono text-xs text-mist-500">
                  {m.base_url || tr("(default endpoint)", "（默认端点）")}
                </div>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-mist-300">
                <input
                  type="checkbox"
                  checked={m.enabled}
                  onChange={async (e) => {
                    await api.adminUpdateModel(m.id, { enabled: e.target.checked });
                    void load();
                  }}
                />
                {m.enabled ? tr("Enabled", "已启用") : tr("Disabled", "已禁用")}
              </label>
              <button
                className="btn-subtle px-2.5 py-1 text-xs"
                title={tr("Test this API key/endpoint", "测试此 API 密钥/端点")}
                disabled={tests[m.id] === "loading"}
                onClick={() => testModel(m.id)}
              >
                {tests[m.id] === "loading" ? <Spinner /> : <Zap size={13} />} {tr("Test", "测试")}
              </button>
              <button
                className="btn-subtle px-2 py-1"
                title={tr("Edit model", "编辑模型")}
                onClick={() => (editingId === m.id ? setEditingId(null) : startEdit(m))}
              >
                <Pencil size={15} />
              </button>
              <button
                className="btn-subtle px-2 py-1 text-rose-300"
                title={tr("Delete model", "删除模型")}
                onClick={async () => {
                  await api.adminDeleteModel(m.id);
                  void load();
                }}
              >
                <Trash2 size={15} />
              </button>
            </div>
           </div>
           {tests[m.id] && tests[m.id] !== "loading" && (
             <div
               className={`whitespace-pre-line border-t border-white/[0.06] px-4 py-2 text-xs ${
                 (tests[m.id] as ApiTestResult).ok ? "text-emerald-300" : "text-rose-300"
               }`}
             >
               {(tests[m.id] as ApiTestResult).ok ? "✓ " : "✗ "}
               {(tests[m.id] as ApiTestResult).detail}
             </div>
           )}
           {editingId === m.id && (
             <div className="border-t border-white/[0.06] p-4">
               <div className="mb-3 text-sm font-medium text-white">{tr("Edit model", "编辑模型")}</div>
               <ModelFormFields
                 form={editForm}
                 setForm={setEditForm}
                 keyPlaceholder={tr("leave blank to keep the current key", "留空则保留当前密钥")}
               />
               <div className="mt-4 flex items-center gap-3">
                 <button className="btn-primary" onClick={saveEdit} disabled={editBusy}>
                   {editBusy ? <Spinner /> : <Save size={15} />} {tr("Save changes", "保存修改")}
                 </button>
                 <button className="btn-subtle px-3 py-1.5 text-sm" onClick={() => setEditingId(null)} disabled={editBusy}>
                   <X size={14} /> {tr("Cancel", "取消")}
                 </button>
                 {editErr && <span className="text-sm text-rose-300">{editErr}</span>}
               </div>
             </div>
           )}
          </div>
        ))}
      </div>

      {/* Add model */}
      <div className="card p-5">
        <div className="mb-3 font-medium text-white">{tr("Add a model", "添加模型")}</div>
        <ModelFormFields
          form={form}
          setForm={setForm}
          keyPlaceholder={tr("stored encrypted; never shown again", "加密存储；不再次显示")}
        />
        <div className="mt-4 flex items-center gap-3">
          <button className="btn-primary" onClick={create} disabled={busy}>
            {busy ? <Spinner /> : <Plus size={15} />} {tr("Add model", "添加模型")}
          </button>
          {err && <span className="text-sm text-rose-300">{err}</span>}
        </div>
      </div>
    </div>
  );
}

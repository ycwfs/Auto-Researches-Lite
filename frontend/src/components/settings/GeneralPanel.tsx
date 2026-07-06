import { Check, KeyRound, Moon, Palette, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { IconTile, PageLoader, Spinner } from "../ui";
import { api, type Credential } from "../../lib/api";
import { useLang } from "../../lib/lang";
import { useTheme } from "../../lib/theme";

/** General tab: local appearance (theme) + the Zotero connection credential. */
export function GeneralPanel() {
  const { t } = useLang();
  const [creds, setCreds] = useState<Credential[] | null>(null);
  const load = () => api.listCredentials().then(setCreds);
  useEffect(() => {
    void load();
  }, []);

  if (!creds) return <PageLoader />;
  const byProvider = Object.fromEntries(creds.map((c) => [c.provider, c]));

  return (
    <div className="space-y-6">
      <AppearanceCard />
      <CredCard
        icon={<KeyRound size={18} />}
        title="Zotero"
        desc={t(
          "Connect your Zotero library to browse collections and sync discovered papers.",
          "连接您的 Zotero 文献库，以浏览分类并同步发现的论文。",
        )}
        fields={[
          { name: "api_key", label: t("API key", "API 密钥"), type: "password" },
          { name: "library_id", label: t("Library ID (numeric userID or groupID)", "文献库 ID（数字 userID 或 groupID）"), type: "text" },
          { name: "library_type", label: t("Library type", "文献库类型"), type: "select", options: ["user", "group"] },
        ]}
        provider="zotero"
        required={["api_key", "library_id"]}
        current={byProvider["zotero"]}
        onSaved={load}
      />
    </div>
  );
}

function AppearanceCard() {
  const { t } = useLang();
  const { theme, setTheme } = useTheme();
  const options: { key: "dark" | "light"; label: string; icon: React.ReactNode }[] = [
    { key: "dark", label: t("Dark", "深色"), icon: <Moon size={15} /> },
    { key: "light", label: t("Light", "浅色"), icon: <Sun size={15} /> },
  ];
  return (
    <div className="card p-5">
      <div className="mb-3.5 flex items-center gap-2.5">
        <IconTile icon={<Palette size={18} />} tone="iris" size="sm" />
        <div>
          <div className="font-medium text-mist-100">{t("Appearance", "外观")}</div>
          <div className="text-xs text-mist-500">
            {t("Choose a theme. Your choice is saved on this device and persists across refreshes.", "选择一个主题。您的选择会保存在本设备上，并在刷新后保持不变。")}
          </div>
        </div>
      </div>
      <div className="flex gap-2">
        {options.map((o) => (
          <button
            key={o.key}
            onClick={() => setTheme(o.key)}
            className={`inline-flex items-center gap-2 rounded-[10px] border px-4 py-2 text-sm transition ${
              theme === o.key
                ? "border-iris-400 bg-iris-500/15 text-mist-100"
                : "border-white/[0.06] text-mist-300 hover:bg-white/[0.05]"
            }`}
          >
            {o.icon} {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

interface FieldDef {
  name: string;
  label: string;
  type: "text" | "password" | "textarea" | "select";
  options?: string[];
}

/** A single-provider encrypted-credential form (Fernet-backed api.setCredential).
 * A field already stored (masked) may be left blank to keep it. */
function CredCard({
  icon,
  title,
  desc,
  fields,
  provider,
  required,
  current,
  onSaved,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  fields: FieldDef[];
  provider: string;
  required: string[];
  current?: Credential;
  onSaved: () => void;
}) {
  const { t } = useLang();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    const missing = required.filter((k) => !values[k]?.trim() && !current?.masked?.[k]);
    if (missing.length) {
      alert(t(`Please fill required fields: ${missing.join(", ")}`, `请填写必填字段：${missing.join("、")}`));
      return;
    }
    setBusy(true);
    setSaved(false);
    try {
      const data = { ...values };
      if (provider === "zotero" && !data.library_type) data.library_type = "user";
      await api.setCredential(provider, data);
      setValues({});
      setSaved(true);
      onSaved();
    } catch (e) {
      alert(e instanceof Error ? e.message : t("Failed to save", "保存失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card p-5">
      <div className="mb-3.5 flex items-center gap-2.5">
        <IconTile icon={icon} tone="iris" size="sm" />
        <div className="flex-1">
          <div className="flex items-center gap-2 font-medium text-white">
            {title}
            {current?.configured && (
              <span className="chip border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
                <Check size={12} /> {t("connected", "已连接")}
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-mist-500">{desc}</div>
        </div>
      </div>

      <div className="grid gap-3.5 sm:grid-cols-2">
        {fields.map((f) => (
          <div key={f.name} className={f.type === "textarea" ? "sm:col-span-2" : ""}>
            <label className="label">
              {f.label}
              {current?.masked?.[f.name] && (
                <span className="ml-2 font-mono normal-case tracking-normal text-mist-500">
                  ({current.masked[f.name]})
                </span>
              )}
            </label>
            {f.type === "textarea" ? (
              <textarea className="input h-24 font-mono text-xs" value={values[f.name] ?? ""}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
            ) : f.type === "select" ? (
              <select className="input" value={values[f.name] ?? f.options?.[0] ?? ""}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}>
                {(f.options ?? []).map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input className="input" type={f.type} value={values[f.name] ?? ""}
                onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
                placeholder={current?.masked?.[f.name] ? t("Leave blank to keep", "留空则保持不变") : ""} />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy}>
          {busy && <Spinner />} {t("Save", "保存")}
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-300">
            <Check size={15} /> {t("Saved.", "已保存。")}
          </span>
        )}
      </div>
    </div>
  );
}

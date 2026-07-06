import { Cpu, Database, Gauge, Plug, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { SectionHeading } from "../components/layout/AppShell";
import { Segmented } from "../components/ui";
import { ConferencesPanel } from "../components/settings/ConferencesPanel";
import { GeneralPanel } from "../components/settings/GeneralPanel";
import { IntegrationsPanel } from "../components/settings/IntegrationsPanel";
import { ModelsPanel } from "../components/settings/ModelsPanel";
import { RuntimePanel } from "../components/settings/RuntimePanel";
import { SourcesPanel } from "../components/settings/SourcesPanel";
import { useLang } from "../lib/lang";

type Tab = "general" | "models" | "integrations" | "sources" | "runtime";

export default function Settings() {
  const { t } = useLang();
  const [tab, setTab] = useState<Tab>("general");

  return (
    <div className="animate-fade-up">
      <SectionHeading
        eyebrow={t("Settings", "设置")}
        title={t("Settings", "设置")}
        desc={t(
          "Everything runs locally for you. Configure appearance, model keys, paper sources, PDF parsing, and runtime — all encrypted at rest.",
          "一切都在本地为你运行。配置外观、模型密钥、论文源、PDF 解析及运行时——所有密钥均加密存储。",
        )}
      />
      <div className="mb-6">
        <Segmented<Tab>
          value={tab}
          onChange={setTab}
          options={[
            { id: "general", label: t("General", "通用"), icon: <SlidersHorizontal size={15} /> },
            { id: "models", label: t("Models", "模型"), icon: <Cpu size={15} /> },
            { id: "integrations", label: t("Integrations", "集成"), icon: <Plug size={15} /> },
            { id: "sources", label: t("Sources", "论文源"), icon: <Database size={15} /> },
            { id: "runtime", label: t("Runtime", "运行时"), icon: <Gauge size={15} /> },
          ]}
        />
      </div>

      {tab === "general" && <GeneralPanel />}
      {tab === "models" && <ModelsPanel />}
      {tab === "integrations" && <IntegrationsPanel />}
      {tab === "sources" && (
        <div className="space-y-8">
          <SourcesPanel />
          <ConferencesPanel />
        </div>
      )}
      {tab === "runtime" && <RuntimePanel />}
    </div>
  );
}

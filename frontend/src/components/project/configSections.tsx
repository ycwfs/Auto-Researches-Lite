/**
 * Shared, controlled project-configuration sections.
 *
 * The project *creation* wizard (Dashboard) and the project *settings* page render
 * the exact same source/conference/Semantic-Scholar/schedule/model controls, so they
 * live here once as controlled components (value + setter props) instead of being
 * duplicated. The data hook fetches the admin-enabled sources, the AI Paper Finder
 * venue catalog, and the model catalog once per mount.
 */
import { Check, Clock, Cpu, Database, Library, SlidersHorizontal } from "lucide-react";
import { type Dispatch, type SetStateAction, useEffect, useMemo, useState } from "react";
import {
  api,
  type DiscoverySchedule,
  type ModelOption,
  type PaperfinderVenue,
  type StepModel,
} from "../../lib/api";
import { useLang } from "../../lib/lang";
import { IconTile } from "../ui";

export const SOURCES = [
  { key: "arxiv", label: "arXiv" },
  { key: "semantic_scholar", label: "Semantic Scholar" },
  { key: "ai_paper_finder", label: "AI Paper Finder" },
];

// Schedule time zones (IANA tz + a friendly "offset · abbrev · country" label).
export const TIMEZONES: { tz: string; label: string }[] = [
  { tz: "UTC", label: "UTC±0 · UTC" },
  { tz: "Asia/Shanghai", label: "UTC+8 · CST · China" },
  { tz: "Asia/Tokyo", label: "UTC+9 · JST · Japan" },
  { tz: "Asia/Singapore", label: "UTC+8 · SGT · Singapore" },
  { tz: "Asia/Kolkata", label: "UTC+5:30 · IST · India" },
  { tz: "Europe/London", label: "UTC+0 · GMT · UK" },
  { tz: "Europe/Paris", label: "UTC+1 · CET · France/Germany" },
  { tz: "Europe/Moscow", label: "UTC+3 · MSK · Russia" },
  { tz: "America/New_York", label: "UTC-5 · EST · US East" },
  { tz: "America/Chicago", label: "UTC-6 · CST · US Central" },
  { tz: "America/Los_Angeles", label: "UTC-8 · PST · US West" },
  { tz: "Australia/Sydney", label: "UTC+10 · AEST · Australia" },
];

// Steps that can have a per-project Channel-A (api) model assigned.
export const STEPS = ["summary", "chat", "zotero"] as const;
export const REASONING_LEVELS = ["off", "low", "medium", "high", "xhigh", "max"] as const;

/** Fetch the enabled sources, AI Paper Finder venues, and model catalog once. */
export function useProjectConfigData() {
  const [availVenues, setAvailVenues] = useState<PaperfinderVenue[]>([]);
  const [availSources, setAvailSources] = useState<{ key: string; name: string }[]>([]);
  const [catalog, setCatalog] = useState<ModelOption[]>([]);
  useEffect(() => {
    api.listModels().then(setCatalog).catch(() => setCatalog([]));
    api.paperfinderVenues().then((r) => setAvailVenues(r.venues)).catch(() => setAvailVenues([]));
    api.sources().then(setAvailSources).catch(() => setAvailSources([]));
  }, []);
  return { availVenues, availSources, catalog };
}

/**
 * Migrate legacy bare-venue selections ("CVPR") to the year-granular ids the picker now
 * uses ("CVPR 2026", "CVPR 2025"). Without this, an old project's bare token matches no
 * chip — it's invisible/unmanageable and can coexist incoherently with a year token.
 */
export function useBareVenueMigration(
  availVenues: PaperfinderVenue[],
  setPfVenues: Dispatch<SetStateAction<string[]>>,
) {
  useEffect(() => {
    if (availVenues.length === 0) return;
    setPfVenues((prev) => {
      const ids = availVenues.map((v) => (v.year ? `${v.venue} ${v.year}` : v.venue));
      const idSet = new Set(ids);
      let changed = false;
      const out: string[] = [];
      for (const tok of prev) {
        if (idSet.has(tok)) {
          out.push(tok); // already a known id (year-granular or genuinely year-less)
          continue;
        }
        const years = ids.filter((id) => id === tok || id.startsWith(`${tok} `));
        if (years.length > 0) {
          changed = true;
          out.push(...years); // bare venue -> all its known years
        } else {
          out.push(tok); // unknown (admin removed it) — keep so the user can still clear it
        }
      }
      const deduped = Array.from(new Set(out));
      return changed || deduped.length !== prev.length ? deduped : prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availVenues]);
}

type SourceMax = Record<string, number>;

/** Paper sources picker + per-source target count. */
export function PaperSourcesSection({
  availSources,
  sources,
  setSources,
  srcMax,
  setSrcMax,
  defaultMax,
}: {
  availSources: { key: string; name: string }[];
  sources: string[];
  setSources: Dispatch<SetStateAction<string[]>>;
  srcMax: SourceMax;
  setSrcMax: Dispatch<SetStateAction<SourceMax>>;
  defaultMax: number;
}) {
  const { t } = useLang();
  // Offer admin-enabled sources, plus any source still selected but since disabled
  // by the admin (shown as disabled so the user knows it is skipped at discovery).
  const pickerSources = useMemo(() => {
    const enabled = availSources.length
      ? availSources.map((s) => ({ key: s.key, label: s.name }))
      : SOURCES;
    const list = enabled.map((s) => ({ ...s, disabled: false }));
    for (const k of sources) {
      if (!list.some((s) => s.key === k)) {
        list.push({ key: k, label: SOURCES.find((s) => s.key === k)?.label || k, disabled: true });
      }
    }
    return list;
  }, [availSources, sources]);

  const toggleSource = (key: string) =>
    setSources((s) => (s.includes(key) ? s.filter((x) => x !== key) : [...s, key]));

  return (
    <div className="card p-5">
      <div className="mb-4 flex items-center gap-2.5">
        <IconTile icon={<Database size={16} />} tone="cyan" size="sm" />
        <div className="font-display font-medium text-mist-100">{t("Paper sources", "论文来源")}</div>
      </div>
      <div className="space-y-2.5">
        {pickerSources.map((s) => {
          const on = sources.includes(s.key);
          return (
            <div key={s.key} className="flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => toggleSource(s.key)}
                title={s.disabled ? t("Disabled by the admin — discovery will skip it", "已被管理员禁用——发现阶段将跳过它") : undefined}
                className={`chip ${
                  s.disabled
                    ? "text-amber-300/80 ring-1 ring-amber-400/30"
                    : on
                      ? "text-iris-300 ring-1 ring-iris-400/40"
                      : "text-mist-500"
                }`}
              >
                {on && <Check size={12} />} {s.label}
                {s.disabled && <span className="ml-1 text-[10px] text-amber-300/70">{t("disabled", "已禁用")}</span>}
              </button>
              <label className={`flex items-center gap-2 text-xs ${on ? "text-mist-300" : "text-mist-500"}`}>
                {t("target", "目标数")}
                <input
                  className="input w-20"
                  type="number"
                  min={1}
                  max={100}
                  disabled={!on}
                  value={srcMax[s.key] ?? defaultMax}
                  onChange={(e) => setSrcMax({ ...srcMax, [s.key]: Number(e.target.value) })}
                />
              </label>
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-mist-500">
        {t(
          `Target papers fetched per source (falls back to the project default of ${defaultMax}). `,
          `每个来源抓取的目标论文数（缺省时回退到项目默认值 ${defaultMax}）。`,
        )}
        {t("A ", "被")}
        <span className="text-amber-300/80">{t("disabled", "禁用")}</span>
        {t(
          " source is turned off by the admin and skipped at discovery — remove it or ask an admin to enable it.",
          "的来源已被管理员关闭，发现阶段会跳过它——请将其移除，或请管理员启用它。",
        )}
      </p>
    </div>
  );
}

/** AI Paper Finder relevance threshold + conference-year picker. `dimmed` greys it out
 *  when the source is off. */
export function ConferencesSection({
  availVenues,
  pfVenues,
  setPfVenues,
  minScore,
  setMinScore,
  dimmed = false,
}: {
  availVenues: PaperfinderVenue[];
  pfVenues: string[];
  setPfVenues: Dispatch<SetStateAction<string[]>>;
  minScore: number;
  setMinScore: Dispatch<SetStateAction<number>>;
  dimmed?: boolean;
}) {
  const { t } = useLang();
  const toggleVenue = (v: string) =>
    setPfVenues((s) => (s.includes(v) ? s.filter((x) => x !== v) : [...s, v]));

  return (
    <div className={`card p-5 transition-opacity ${dimmed ? "opacity-60" : ""}`}>
      <div className="mb-2 flex items-center gap-2.5">
        <IconTile icon={<Library size={16} />} tone="cyan" size="sm" />
        <div className="font-display font-medium text-mist-100">AI Paper Finder</div>
      </div>

      {/* Relevance threshold (governs retrieval when > 0). */}
      <div className="mb-4">
        <label className="label flex items-center justify-between">
          <span>{t("Minimum relevance score", "最低相关性分数")}</span>
          <span className="font-mono text-xs text-iris-300">
            {minScore > 0 ? minScore.toFixed(2) : t("off", "关闭")}
          </span>
        </label>
        <input
          type="range"
          min={0}
          max={0.9}
          step={0.05}
          value={minScore}
          onChange={(e) => setMinScore(Number(e.target.value))}
          className="mt-2 w-full accent-iris-500"
        />
        <p className="mt-1 text-xs leading-relaxed text-mist-500">
          {t(
            "0 = off (returns the top results by rank). Above 0, each run keeps only papers scoring at or above this bar and returns the ",
            "0 = 关闭（按排名返回最靠前的结果）。大于 0 时，每次运行只保留分数达到或高于此门槛的论文，并返回其中",
          )}
          <span className="text-mist-300">{t("highest-scoring", "得分最高的")}</span>
          {t(
            " of them, up to the source's max-results bound. So the bar sets quality and max-results caps the count. On this corpus ~0.55+ keeps only strong matches, ~0.40 is looser, <0.20 is noise.",
            "部分，数量上限受该来源的最大结果数约束。因此门槛决定质量，最大结果数限制数量。在本语料库上，~0.55 以上仅保留强相关结果，~0.40 较为宽松，<0.20 即为噪声。",
          )}
        </p>
      </div>

      <label className="label">{t("Conferences", "会议")}</label>
      <p className="mb-3 text-xs leading-relaxed text-mist-500">
        {t(
          "Restrict the semantic search to these conference-years. None selected = search all.",
          "将语义检索限制在这些会议年份内。未选择任何项 = 检索全部。",
        )}
      </p>
      {availVenues.length === 0 ? (
        <p className="text-xs text-mist-500">
          {t(
            "No conferences available yet — an admin can add them in the admin panel.",
            "暂无可用会议——管理员可在管理面板中添加。",
          )}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {[...availVenues]
            .sort((a, b) =>
              a.venue === b.venue
                ? (b.year ?? "").localeCompare(a.year ?? "")
                : a.venue.localeCompare(b.venue),
            )
            .map((v) => {
              const id = v.year ? `${v.venue} ${v.year}` : v.venue;
              const on = pfVenues.includes(id);
              return (
                <button
                  type="button"
                  key={id}
                  onClick={() => toggleVenue(id)}
                  className={`chip ${on ? "text-iris-300 ring-1 ring-iris-400/40" : "text-mist-500"}`}
                  title={t(`${v.count} papers`, `${v.count} 篇论文`)}
                >
                  {on && <Check size={12} />} {id}
                  <span className="ml-1 font-mono text-[10px] text-mist-500">{v.count}</span>
                </button>
              );
            })}
        </div>
      )}
    </div>
  );
}

/** Semantic Scholar per-project tuning. */
export function SemanticScholarSection({
  s2Recency,
  setS2Recency,
  s2MinCit,
  setS2MinCit,
  s2Fields,
  setS2Fields,
}: {
  s2Recency: number;
  setS2Recency: Dispatch<SetStateAction<number>>;
  s2MinCit: number;
  setS2MinCit: Dispatch<SetStateAction<number>>;
  s2Fields: string;
  setS2Fields: Dispatch<SetStateAction<string>>;
}) {
  const { t } = useLang();
  return (
    <div className="card p-5">
      <div className="mb-2 flex items-center gap-2.5">
        <IconTile icon={<SlidersHorizontal size={16} />} tone="cyan" size="sm" />
        <div className="font-display font-medium text-mist-100">Semantic Scholar</div>
      </div>
      <p className="mb-4 text-xs leading-relaxed text-mist-500">
        {t(
          "Tune Semantic Scholar results for this project. Field of study: blank or 'auto' derives from your categories, 'off' disables it, or set an explicit field like 'Computer Science'.",
          "为本项目调优 Semantic Scholar 的检索结果。研究领域：留空或填 'auto' 会从你的类别推导，填 'off' 则禁用，或填入明确的领域，如 'Computer Science'。",
        )}
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="label">{t("Recency (days, 0 = no limit)", "时效（天，0 = 不限）")}</span>
          <input className="input mt-1.5" type="number" min={0} value={s2Recency}
            onChange={(e) => setS2Recency(Number(e.target.value))} />
        </label>
        <label className="text-sm">
          <span className="label">{t("Min citations (0 = none)", "最少引用数（0 = 不限）")}</span>
          <input className="input mt-1.5" type="number" min={0} value={s2MinCit}
            onChange={(e) => setS2MinCit(Number(e.target.value))} />
        </label>
        <label className="text-sm">
          <span className="label">{t("Field of study", "研究领域")}</span>
          <input className="input mt-1.5" type="text" placeholder="auto / off / Computer Science"
            value={s2Fields} onChange={(e) => setS2Fields(e.target.value)} />
        </label>
      </div>
    </div>
  );
}

/** Daily-fetch schedule + the project's total-paper cap. */
export function SchedulesSection({
  disc,
  setDisc,
  capPapers,
  setCapPapers,
}: {
  disc: DiscoverySchedule;
  setDisc: Dispatch<SetStateAction<DiscoverySchedule>>;
  capPapers: number;
  setCapPapers: Dispatch<SetStateAction<number>>;
}) {
  const { t } = useLang();
  return (
    <div className="card p-5">
      <div className="mb-4 flex items-center gap-2.5">
        <IconTile icon={<Clock size={16} />} tone="cyan" size="sm" />
        <div className="font-display font-medium text-mist-100">{t("Schedules", "计划任务")}</div>
      </div>
      <div className="space-y-4">
        <label className="flex flex-wrap items-center gap-3 text-sm text-mist-100">
          {t("Time zone", "时区")}
          <select
            className="input w-auto min-w-[15rem]"
            value={disc.tz || "UTC"}
            onChange={(e) => setDisc((d) => ({ ...d, tz: e.target.value }))}
          >
            {TIMEZONES.map((t) => (
              <option key={t.tz} value={t.tz}>{t.label}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-wrap items-center gap-3 text-sm text-mist-100">
          <input
            type="checkbox"
            className="accent-iris-500"
            checked={!!disc.enabled}
            onChange={(e) => setDisc({ ...disc, enabled: e.target.checked })}
          />
          {t("Daily paper fetch at", "每日抓取论文时间")}
          <input
            className="input w-28"
            type="time"
            value={disc.time_utc || "08:00"}
            onChange={(e) => setDisc({ ...disc, time_utc: e.target.value })}
          />
        </label>
        <label className="flex flex-wrap items-center gap-3 text-sm text-mist-100">
          {t("Max total papers (0–600, caps the project to keep the DB bounded):", "论文总数上限（0–600，限制项目规模以控制数据库体量）：")}
          <input
            className="input w-24"
            type="number"
            min={0}
            max={600}
            value={capPapers}
            onChange={(e) => setCapPapers(Math.min(600, Math.max(0, Number(e.target.value))))}
          />
        </label>
      </div>
    </div>
  );
}

/** Per-step model + reasoning-effort picker. */
export function PerStepModelSection({
  catalog,
  models,
  setModels,
}: {
  catalog: ModelOption[];
  models: Record<string, StepModel>;
  setModels: Dispatch<SetStateAction<Record<string, StepModel>>>;
}) {
  const { t } = useLang();
  return (
    <div className="card p-5">
      <div className="mb-2 flex items-center gap-2.5">
        <IconTile icon={<Cpu size={16} />} tone="cyan" size="sm" />
        <div className="font-display font-medium text-mist-100">{t("Per-step model", "分步模型")}</div>
      </div>
      <p className="mb-4 text-xs leading-relaxed text-mist-500">
        {t(
          "Choose from models your administrator has enabled for your plan. Leave on “default” to let the platform pick. Reasoning effort applies to API models only.",
          "从管理员为你的套餐启用的模型中选择。保持“默认”则由平台自动挑选。推理强度仅对 API 模型生效。",
        )}
        {catalog.length === 0 &&
          t(
            " (No models available yet — ask an admin to add some.)",
            "（暂无可用模型——请联系管理员添加。）",
          )}
      </p>
      <div className="space-y-3">
        {STEPS.map((step) => {
          const options = catalog;
          const picked = catalog.find((m) => m.id === models[step]?.model_id);
          // Only offer the effort levels the picked model advertises (empty → off only).
          const supported = picked?.supported_efforts ?? [];
          const effortLevels = ["off", ...REASONING_LEVELS.filter((r) => r !== "off" && supported.includes(r))];
          const reasoningDisabled = !picked || supported.length === 0;
          const currentReasoning = models[step]?.reasoning ?? "off";
          const shownReasoning = effortLevels.includes(currentReasoning) ? currentReasoning : "off";
          return (
            <div key={step} className="grid grid-cols-[84px,1fr,150px] items-center gap-2">
              <span className="text-sm capitalize text-mist-300">{step}</span>
              <select
                className="input"
                value={models[step]?.model_id ?? ""}
                onChange={(e) =>
                  setModels({
                    ...models,
                    [step]: {
                      ...models[step],
                      model_id: e.target.value ? Number(e.target.value) : undefined,
                    },
                  })
                }
              >
                <option value="">{t("default (auto)", "默认（自动）")}</option>
                {options.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} · {m.model}
                    {m.key_set ? "" : t(" · no key (mock)", " · 无密钥（模拟）")}
                    {m.test_failed ? t(" · test failed", " · 测试失败") : ""}
                  </option>
                ))}
              </select>
              <select
                className="input"
                title={t("Reasoning effort (API models)", "推理强度（API 模型）")}
                disabled={reasoningDisabled}
                value={shownReasoning}
                onChange={(e) =>
                  setModels({
                    ...models,
                    [step]: {
                      ...models[step],
                      reasoning: e.target.value as StepModel["reasoning"],
                    },
                  })
                }
              >
                {effortLevels.map((r) => (
                  <option key={r} value={r}>
                    {r === "off" ? t("no reasoning", "不推理") : r}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
      </div>
    </div>
  );
}

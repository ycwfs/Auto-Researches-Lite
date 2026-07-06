import { Languages } from "lucide-react";
import { useLang } from "../lib/lang";

/**
 * Compact English / 中文 language toggle — sits next to the theme toggle and switches the
 * whole UI. The choice persists in localStorage (key far-lang).
 */
export function LanguageToggle({ className = "" }: { className?: string }) {
  const { lang, setLang } = useLang();
  const opts: { id: "en" | "zh"; label: string }[] = [
    { id: "en", label: "EN" },
    { id: "zh", label: "中" },
  ];
  return (
    <div
      role="group"
      aria-label="Language / 语言"
      className={`inline-flex items-center gap-0.5 rounded-full border border-white/[0.06] bg-ink-900/40 p-0.5 ${className}`}
    >
      <Languages size={14} className="ml-1 text-mist-500" aria-hidden />
      {opts.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => setLang(o.id)}
          aria-pressed={lang === o.id}
          title={o.id === "en" ? "English" : "中文"}
          className={`flex items-center rounded-full px-2.5 py-1 text-xs transition ${
            lang === o.id ? "bg-iris-500/20 text-white" : "text-mist-500 hover:text-mist-100"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

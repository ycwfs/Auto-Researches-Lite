import { Moon, Sun } from "lucide-react";
import { useTheme } from "../lib/theme";

/**
 * Compact dark/light theme toggle — always visible, so switching the theme never
 * requires opening the account menu. Default is dark.
 */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const opts: { id: "dark" | "light"; icon: React.ReactNode; label: string }[] = [
    { id: "dark", icon: <Moon size={14} />, label: "Dark" },
    { id: "light", icon: <Sun size={14} />, label: "Light" },
  ];
  return (
    <div
      role="group"
      aria-label="Theme"
      className={`inline-flex gap-0.5 rounded-full border border-white/[0.06] bg-ink-900/40 p-0.5 ${className}`}
    >
      {opts.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => setTheme(o.id)}
          aria-pressed={theme === o.id}
          title={`${o.label} theme`}
          className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition ${
            theme === o.id ? "bg-iris-500/20 text-white" : "text-mist-500 hover:text-mist-100"
          }`}
        >
          {o.icon}
        </button>
      ))}
    </div>
  );
}

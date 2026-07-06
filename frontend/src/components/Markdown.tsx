import { Suspense, lazy } from "react";

// Code-split the markdown+KaTeX renderer so it only loads when a summary is shown.
const MarkdownView = lazy(() => import("./MarkdownView"));

/**
 * Render LLM markdown (e.g. the 5-point paper summary) with GitHub-flavored markdown
 * (headings, bold, lists, tables) and LaTeX math ($…$ / $$…$$), styled for the dark
 * theme — replacing the raw text where `#`, `**`, and formulas previously leaked.
 */
export function Markdown({
  children,
  className = "break-words text-xs leading-relaxed text-mist-300",
}: {
  children: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <Suspense fallback={<span className="whitespace-pre-wrap text-mist-300">{children}</span>}>
        <MarkdownView>{children}</MarkdownView>
      </Suspense>
    </div>
  );
}

import "katex/dist/katex.min.css";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

/**
 * Heavy markdown+math renderer (react-markdown + KaTeX). Loaded lazily via `Markdown`
 * so KaTeX (~200 KB) only ships when a user actually expands a summary.
 */
const components: Components = {
  h1: ({ node, ...p }) => <h1 className="mb-1.5 mt-3 font-display text-sm font-semibold text-white" {...p} />,
  h2: ({ node, ...p }) => <h2 className="mb-1 mt-3 font-display text-[13px] font-semibold text-mist-100" {...p} />,
  h3: ({ node, ...p }) => <h3 className="mb-1 mt-2 text-xs font-semibold text-mist-100" {...p} />,
  p: ({ node, ...p }) => <p className="my-1.5 leading-relaxed" {...p} />,
  ul: ({ node, ...p }) => <ul className="my-1.5 list-disc space-y-1 pl-5" {...p} />,
  ol: ({ node, ...p }) => <ol className="my-1.5 list-decimal space-y-1 pl-5" {...p} />,
  li: ({ node, ...p }) => <li className="leading-relaxed" {...p} />,
  strong: ({ node, ...p }) => <strong className="font-semibold text-mist-100" {...p} />,
  em: ({ node, ...p }) => <em className="italic" {...p} />,
  a: ({ node, ...p }) => (
    <a className="text-iris-300 underline hover:text-iris-400" target="_blank" rel="noreferrer" {...p} />
  ),
  code: ({ node, ...p }) => (
    <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[11px] text-cyan-300" {...p} />
  ),
  hr: () => <hr className="my-3 border-white/[0.06]" />,
  blockquote: ({ node, ...p }) => (
    <blockquote className="my-2 border-l-2 border-white/10 pl-3 text-mist-400" {...p} />
  ),
  table: ({ node, ...p }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs" {...p} />
    </div>
  ),
  th: ({ node, ...p }) => (
    <th className="border border-white/[0.08] px-2 py-1 text-left font-medium text-mist-200" {...p} />
  ),
  td: ({ node, ...p }) => <td className="border border-white/[0.08] px-2 py-1" {...p} />,
};

export default function MarkdownView({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={components}
    >
      {children}
    </ReactMarkdown>
  );
}

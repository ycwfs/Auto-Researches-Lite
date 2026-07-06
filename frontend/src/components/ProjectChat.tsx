import { Bot, CornerDownRight, Send, User, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type ChatMessage } from "../lib/api";
import { useLang } from "../lib/lang";
import { IconTile, Spinner } from "./ui";

/** Project-aware starting points that fill the input when tapped. */
const STARTER_PROMPTS: readonly { en: string; zh: string }[] = [
  { en: "Which papers should I prioritize?", zh: "我应该优先阅读哪些论文？" },
  { en: "What should I do next in this project?", zh: "这个项目接下来我该做什么？" },
  { en: "What are the emerging trends across these papers?", zh: "这些论文中有哪些新兴趋势？" },
];

/**
 * Project assistant. Rendered as a docked, full-height rail on the right edge —
 * a flex sibling of the main content (see AppShell), so it sits *beside* the
 * project instead of overlaying it, and the content keeps the rest of the width.
 * Toggled open from the "Assistant" button in ProjectLayout.
 */
export function ProjectChat({
  projectId,
  onClose,
  width,
  onResizePointerDown,
  onResizeReset,
}: {
  projectId: number;
  onClose: () => void;
  /** User-draggable panel width (px); the handle on the left edge resizes it. */
  width?: number;
  onResizePointerDown?: (e: React.PointerEvent) => void;
  onResizeReset?: () => void;
}) {
  const { lang, t } = useLang();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Move focus into the panel on open; restore it to the opener on close.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => prev?.focus?.();
  }, []);

  useEffect(() => {
    let alive = true;
    setMessages([]); // drop the previous project's thread while the new one loads
    api
      .chatHistory(projectId)
      .then((m) => {
        if (alive) setMessages(m);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [projectId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [
      ...m,
      { id: Date.now(), role: "user", content: text, created_at: "" },
    ]);
    try {
      const reply = await api.chatSend(projectId, text);
      setMessages((m) => [...m, reply]);
    } catch {
      setMessages((m) => [
        ...m,
        { id: Date.now() + 1, role: "assistant", content: t("(failed to reach assistant)", "（无法连接助手）"), created_at: "" },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Dimmed backdrop only in the overlay regime (< 2xl). */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm 2xl:hidden"
        onClick={onClose}
        aria-hidden
      />
      <aside
        id="project-assistant-panel"
        aria-label={t("Project assistant", "项目助手")}
        style={{ width: width ?? 400 }}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
        className="card fixed inset-y-0 right-0 z-50 flex max-w-[100vw] shrink-0 flex-col overflow-hidden rounded-none border-l border-white/[0.06] shadow-glow animate-slide-in-right 2xl:relative 2xl:z-auto 2xl:shadow-none"
      >
      {onResizePointerDown && (
        <div
          role="separator"
          aria-orientation="vertical"
          onPointerDown={onResizePointerDown}
          onDoubleClick={onResizeReset}
          title={t("Drag to resize · double-click to reset", "拖动调整宽度 · 双击重置")}
          className="group absolute inset-y-0 left-0 z-30 w-2 cursor-col-resize"
        >
          <span className="absolute inset-y-0 left-0 w-px bg-white/[0.06] transition-colors group-hover:bg-iris-500/60" />
        </div>
      )}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
        <div className="flex items-center gap-2.5 font-display text-sm font-medium text-mist-100">
          <IconTile icon={<Bot size={15} />} tone="brand" size="sm" />
          {t("Project assistant", "项目助手")}
        </div>
        <button className="btn-subtle px-2 py-1" onClick={onClose} aria-label={t("Hide assistant", "隐藏助手")}>
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="mt-2">
            <p className="text-[13px] leading-relaxed text-mist-500">
              {t(
                "I can see this project's papers and research context. Ask me anything — here are a few ",
                "我能看到这个项目的论文和研究背景。尽管问我——这里有几个",
              )}
              <span className="text-mist-300">{t("starting points", "起点")}</span>
              {t(":", "：")}
            </p>
            <div className="mt-3.5 flex flex-col gap-2">
              {STARTER_PROMPTS.map((p) => (
                <button
                  key={p.en}
                  type="button"
                  onClick={() => setInput(lang === "zh" ? p.zh : p.en)}
                  className="flex items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-2.5 text-left text-[12.5px] text-mist-100 transition-colors hover:border-iris-500/30 hover:bg-iris-500/10"
                >
                  <CornerDownRight size={13} className="shrink-0 text-iris-300" />
                  {lang === "zh" ? p.zh : p.en}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex gap-2 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
            <div
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                m.role === "user" ? "bg-iris-500/20 text-iris-300" : "bg-cyan-500/15 text-cyan-400"
              }`}
            >
              {m.role === "user" ? <User size={14} /> : <Bot size={14} />}
            </div>
            <div
              className={`max-w-[82%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-[13px] leading-relaxed ${
                m.role === "user"
                  ? "bg-iris-500/15 text-mist-100"
                  : "border border-white/[0.06] bg-white/[0.03] text-mist-300"
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-[13px] text-mist-500">
            <Spinner /> {t("thinking…", "思考中…")}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-white/[0.06] p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            className="input max-h-28 min-h-[40px] flex-1 resize-none text-[13px]"
            placeholder={t("Message the assistant…", "给助手发消息…")}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <button
            className="btn-primary shrink-0 px-3 py-2"
            onClick={send}
            disabled={busy}
            aria-label={t("Send message", "发送消息")}
          >
            <Send size={16} />
          </button>
        </div>
      </div>
      </aside>
    </>
  );
}

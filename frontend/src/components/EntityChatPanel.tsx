import { Bot, MessageSquare, Send, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type ChatMessage, type EntityScope } from "../lib/api";
import { useLang } from "../lib/lang";
import { Spinner } from "./ui";

/**
 * Collapsible chat thread scoped to ONE discovered paper. The model is grounded
 * server-side in that paper's own context (its full text + summary + code notes);
 * the context itself isn't shown here — you edit the paper directly.
 */
export function EntityChatPanel({
  projectId,
  scope,
  entityId,
}: {
  projectId: number;
  scope: EntityScope;
  entityId: number;
}) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || messages.length) return;
    api.chatHistory(projectId, scope, entityId).then(setMessages).catch(() => {});
  }, [open, messages.length, projectId, scope, entityId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);
    setMessages((m) => [...m, { id: Date.now(), role: "user", content: text, created_at: "" }]);
    try {
      const r = await api.chatSend(projectId, text, scope, entityId);
      setMessages((m) => [...m, r]);
    } catch {
      setMessages((m) => [
        ...m,
        { id: Date.now() + 1, role: "assistant", content: t("(failed to reach assistant)", "（无法连接助手）"), created_at: "" },
      ]);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.02]">
      <button
        className="flex w-full items-center justify-between px-3 py-2 text-xs text-mist-300"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="flex items-center gap-1.5">
          <MessageSquare size={13} /> {t("Chat about this paper", "讨论此论文")}
        </span>
        <span className="text-mist-500">{open ? t("Hide", "隐藏") : t("Show", "显示")}</span>
      </button>

      {open && (
        <div className="border-t border-white/[0.06] p-3">
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {messages.length === 0 && (
              <p className="text-xs text-mist-500">{t("Ask about this paper…", "咨询此论文…")}</p>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`flex gap-2 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                <div
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                    m.role === "user" ? "bg-iris-500/20 text-iris-300" : "bg-cyan-500/15 text-cyan-400"
                  }`}
                >
                  {m.role === "user" ? <User size={12} /> : <Bot size={12} />}
                </div>
                <div
                  className={`max-w-[82%] whitespace-pre-wrap rounded-xl px-2.5 py-1.5 text-xs ${
                    m.role === "user"
                      ? "bg-iris-500/15 text-mist-100"
                      : "border border-white/[0.06] bg-white/[0.03] text-mist-300"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex items-center gap-2 text-xs text-mist-500">
                <Spinner /> {t("thinking…", "思考中…")}
              </div>
            )}
            <div ref={endRef} />
          </div>
          <div className="mt-2 flex items-end gap-2">
            <textarea
              className="input max-h-24 min-h-[36px] flex-1 resize-none text-xs"
              placeholder={t("Message about this paper…", "针对此论文发消息…")}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button className="btn-primary shrink-0 px-2.5 py-1.5" onClick={send} disabled={sending}>
              <Send size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * App-level state for the project assistant. The assistant is docked at the
 * AppShell level (a flex sibling of <main>), so opening it shrinks the content
 * column instead of overlaying it. ProjectLayout registers the active project
 * id while it is mounted; leaving a project clears it and hides the panel.
 */
interface AssistantState {
  projectId: number | null;
  open: boolean;
  setOpen: (v: boolean) => void;
  toggle: () => void;
  setProjectId: (id: number | null) => void;
}

const AssistantContext = createContext<AssistantState | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState<number | null>(null);
  const value = useMemo<AssistantState>(
    () => ({
      open,
      setOpen,
      toggle: () => setOpen((o) => !o),
      projectId,
      setProjectId,
    }),
    [open, projectId],
  );
  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}

export function useAssistant(): AssistantState {
  const ctx = useContext(AssistantContext);
  if (!ctx) throw new Error("useAssistant must be used within AssistantProvider");
  return ctx;
}

// Typed API client for the Auto-Researches Lite backend.
//
// Single-user, local edition: there is no login/token flow. Every request acts as
// the auto-created local user, so no Authorization header is attached and 401s
// carry no special meaning (the backend never gates a request behind auth).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    form?: URLSearchParams;
    formData?: FormData;
    raw?: boolean;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = {};

  let body: BodyInit | undefined;
  if (options.formData) {
    // Let the browser set the multipart Content-Type (with boundary).
    body = options.formData;
  } else if (options.form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = options.form.toString();
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const res = await fetch(`/api${path}`, { method: options.method ?? "GET", headers, body });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    // Never surface an empty message: over HTTP/2 `statusText` is always "" and a
    // non-JSON error body (e.g. a 500 through the proxy) leaves `detail` blank,
    // which would render as an empty alert box with no explanation.
    throw new ApiError(res.status, detail || `Request failed (HTTP ${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  if (options.raw) return res as unknown as T;
  return (await res.json()) as T;
}

// ---- Types ----
export interface User {
  id: number;
  email: string;
  full_name: string;
  is_admin: boolean;
  created_at: string;
}
export type ModelKind = "api";
export interface StepModel {
  model_id?: number;
  reasoning?: "off" | "low" | "medium" | "high" | "xhigh" | "max";
}
export interface ModelOption {
  id: number;
  label: string;
  kind: ModelKind;
  provider: string;
  model: string;
  key_set: boolean; // false → no API key configured; this pick falls back to the offline mock
  test_failed: boolean; // true → last admin connectivity test failed (admins only ever see these)
  supported_efforts: string[]; // effort levels this model accepts ([] = no effort → picker shows only "off")
}
export interface AdminModel {
  id: number;
  label: string;
  kind: ModelKind;
  provider: string;
  api_style: string; // "anthropic" | "openai" | "" (=infer from provider)
  base_url: string;
  model: string;
  enabled: boolean;
  supported_efforts: string[];
  key_set: boolean;
  last_test_ok: boolean | null; // null = never tested / stale after connection edits
  last_test_at: string | null;
}
export interface DiscoverySchedule {
  enabled?: boolean;
  time_utc?: string;
  tz?: string;
  last_run?: string;
}
export interface Project {
  id: number;
  name: string;
  description: string;
  categories: string[];
  keywords: string[];
  max_results: number;
  max_total_papers: number | null;
  paper_sources: string[];
  s2_recency_days: number | null;
  s2_fields_of_study: string | null;
  s2_min_citations: number | null;
  paper_finder_venues: string[] | null;
  paper_finder_query: string;
  paper_finder_min_score: number;
  source_max_results: Record<string, number> | null;
  step_models: Record<string, StepModel>;
  discovery_schedule: DiscoverySchedule;
  stage: string;
  created_at: string;
  updated_at: string;
}
export interface Paper {
  id: number;
  arxiv_id: string;
  source: string;
  title: string;
  authors: string[];
  abstract: string;
  categories: string[];
  pdf_url: string;
  published: string;
  venue?: string; // curated conference (e.g. "CVPR") from AI Paper Finder
  summary_en: string;
  summary_zh: string;
  relevance: number;
  finder_score?: number; // AI Paper Finder semantic similarity (cosine); 0 for other sources
  document_id?: number | null;
  code_status?: string; // "ok" when a code-repository analysis is available
  has_fulltext?: boolean; // parsed full text (MinerU/pypdf) present — gates the per-paper chat
  fulltext_recoverable?: boolean; // false once a forced re-parse proved full text is unrecoverable
  created_at?: string | null;
}
export interface PaperDocument {
  id: number;
  arxiv_id: string;
  doi: string;
  title: string;
  authors: string[];
  year: string;
  source: string;
  summary: string;
  extraction_method: string;
  has_markdown: boolean;
  created_at: string;
}
export interface Trends {
  paper_count: number;
  top_keywords: { term: string; weight: number }[];
  categories: Record<string, number>;
  has_wordcloud: boolean;
}
export interface Job {
  id: number;
  project_id: number;
  type: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
  progress: number;
  log: string;
  error: string;
  cancel_requested?: boolean;
  target_id: number | null;
  payload?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export interface Credential {
  provider: string;
  configured: boolean;
  masked: Record<string, string>;
}
export interface PaperfinderVenue {
  venue: string;
  year?: string;
  count: number;
  enabled?: boolean; // admin flag (only relevant in the admin list)
}
export interface ConferenceIngestTask {
  task_id: string;
  state: "queued" | "collecting" | "embedding" | "done" | "failed";
  venue?: string;
  year?: string;
  collected?: number;
  count?: number;
  message?: string;
}
export interface WorkersState {
  stored: number; // admin override (0 = unset, use env)
  env_default: number;
  target: number; // effective per-container concurrency
  live: number; // total live RQ worker processes across containers (-1 = unknown)
}
export interface ZoteroCollection {
  key: string;
  name: string;
  num_items: number;
}
export interface ZoteroItem {
  key: string;
  item_type: string;
  title: string;
  abstract: string;
  url: string;
  date: string;
  creators: string[];
}
export interface ProjectContext {
  background: string;
  references: string;
  papers_summary: string;
  stage: string;
  updated_at: string | null;
}
export interface ChatMessage {
  id: number;
  role: string;
  content: string;
  created_at: string;
}
export type EntityScope = "discovered";
export interface EntityContext {
  scope: string;
  scope_id: number;
  content: string;
  is_custom: boolean;
  updated_at: string | null;
}
/** Query string scoping chat/context to one entity thread (empty for the project thread). */
function _chatScope(scope?: EntityScope, id?: number): string {
  return scope && id ? `?scope=${scope}&scope_id=${id}` : "";
}
export interface AdminSource {
  id: number;
  key: string;
  name: string;
  description: string;
  enabled: boolean;
  config: Record<string, string>;
  key_count: number; // number of stored API keys (the keys themselves are never returned)
}
// Source create/update may carry `api_keys` (a key pool, write-only — never returned).
export type AdminSourceWrite = Partial<Omit<AdminSource, "key_count">> & { api_keys?: string[] };
export interface ProjectPrompt {
  key: string;
  label: string;
  stage: string;
  channel: string;
  contract_note: string;
  placeholders: string[]; // required placeholders that must remain
  placeholder_docs: Record<string, string>; // every placeholder -> its meaning
  default_template: string;
  template: string; // effective (the project's edit, else the default)
  is_custom: boolean;
}
export interface IntegrationConfig {
  mineru_api_url: string;
  mineru_key_set: boolean;
  mineru_max_wait_seconds: number; // 0 = built-in default (120 s)
}
export interface ApiTestResult {
  ok: boolean;
  detail: string;
}

// ---- Endpoints ----
export const api = {
  // local user (auto-created; every request already acts as this user)
  me: () => request<User>("/auth/me"),

  // projects
  listProjects: () => request<Project[]>("/projects"),
  createProject: (data: Partial<Project>) =>
    request<Project>("/projects", { method: "POST", body: data }),
  getProject: (id: number) => request<Project>(`/projects/${id}`),
  exploredPapers: (id: number) => request<PaperDocument[]>(`/projects/${id}/papers`),
  updateProject: (id: number, data: Partial<Project>) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: data }),
  deleteProject: (id: number) => request<void>(`/projects/${id}`, { method: "DELETE" }),

  // discovery
  runDiscovery: (pid: number) =>
    request<Job>(`/projects/${pid}/discovery/run`, { method: "POST" }),
  // Run ONLY the AI Paper Finder (decoupled from the regular/scheduled discovery run).
  runPaperFinder: (pid: number) =>
    request<Job>(`/projects/${pid}/discovery/run/paper-finder`, { method: "POST" }),
  papers: (pid: number) => request<Paper[]>(`/projects/${pid}/discovery/papers`),
  paperSummary: (pid: number, paperId: number) =>
    request<{ summary_5pt: string; code_url: string; code_summary: string; code_status: string }>(
      `/projects/${pid}/discovery/papers/${paperId}/summary`,
    ),
  deletePaper: (pid: number, paperId: number) =>
    request<void>(`/projects/${pid}/discovery/papers/${paperId}`, { method: "DELETE" }),
  // Bulk-remove papers by id (e.g. the selected set); returns how many were deleted.
  deletePapers: (pid: number, paperIds: number[]) =>
    request<{ deleted: number }>(`/projects/${pid}/discovery/papers/delete`, {
      method: "POST",
      body: { paper_ids: paperIds },
    }),
  // Force re-run of a paper's Summary / code analysis with the project's current prompt
  // (prompt-debug); each returns a Job to poll, then re-fetch the paper summary.
  resummarizePaper: (pid: number, paperId: number) =>
    request<Job>(`/projects/${pid}/discovery/papers/${paperId}/resummarize`, { method: "POST" }),
  // Force a fresh MinerU parse then re-summarize — recovers a paper stuck on the
  // abstract fallback (a transient extraction miss / a slow PDF that timed out).
  reparsePaper: (pid: number, paperId: number) =>
    request<Job>(`/projects/${pid}/discovery/papers/${paperId}/reparse`, { method: "POST" }),
  // Last resort: attach a user-uploaded PDF to an existing paper (when its PDF can't
  // be fetched server-side and it isn't on arXiv), extract it, and re-summarize.
  uploadPaperPdf: (pid: number, paperId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Job>(`/projects/${pid}/discovery/papers/${paperId}/upload-pdf`, {
      method: "POST",
      formData: fd,
    });
  },
  // Re-run a paper's code analysis. With repoUrl (the manual "Code Analysis" action) the
  // given GitHub/GitLab repo is analyzed directly; without it the URL is re-detected.
  reanalyzeCode: (pid: number, paperId: number, repoUrl = "") =>
    request<Job>(`/projects/${pid}/discovery/papers/${paperId}/recode`, {
      method: "POST",
      body: { repo_url: repoUrl },
    }),
  // Bulk re-summarize the selected papers (mode "full_text" | "code") in one job.
  resummarizePapers: (pid: number, paperIds: number[], mode: "full_text" | "code") =>
    request<Job>(`/projects/${pid}/discovery/papers/resummarize`, {
      method: "POST",
      body: { paper_ids: paperIds, mode },
    }),
  // Manually add one paper (summarized + stored like a discovered one); returns a Job to poll.
  addPaper: (pid: number, url: string) =>
    request<Job>(`/projects/${pid}/discovery/papers/add`, { method: "POST", body: { url } }),
  uploadPaper: (pid: number, file: File, title = "") => {
    const fd = new FormData();
    fd.append("file", file);
    if (title.trim()) fd.append("title", title.trim());
    return request<Job>(`/projects/${pid}/discovery/papers/upload`, { method: "POST", formData: fd });
  },
  trends: (pid: number) => request<Trends>(`/projects/${pid}/discovery/trends`),
  wordcloudUrl: (pid: number) => `/api/projects/${pid}/discovery/wordcloud`,

  // models (user-facing picker; no keys)
  listModels: () => request<ModelOption[]>("/models"),

  // paper sources — enabled sources for the project picker
  sources: () => request<{ key: string; name: string }[]>("/sources"),
  // paper sources — available AI Paper Finder conference venues
  paperfinderVenues: () => request<{ venues: PaperfinderVenue[] }>("/sources/paperfinder/venues"),

  // context + chat (per-entity context is computed server-side for generation + chat;
  // the project context feeds the project assistant + the Context panel)
  getContext: (pid: number) => request<ProjectContext>(`/projects/${pid}/context`),
  chatHistory: (pid: number, scope?: EntityScope, id?: number) =>
    request<ChatMessage[]>(`/projects/${pid}/chat${_chatScope(scope, id)}`),
  chatSend: (pid: number, message: string, scope?: EntityScope, id?: number) =>
    request<ChatMessage>(`/projects/${pid}/chat${_chatScope(scope, id)}`, {
      method: "POST",
      body: { message },
    }),

  // jobs
  getJob: (id: number) => request<Job>(`/jobs/${id}`),
  cancelJob: (id: number) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  listJobs: (pid?: number) =>
    request<Job[]>(`/jobs${pid ? `?project_id=${pid}` : ""}`),

  // Curated .zip of the project's source/context files (context doc + summaries).
  // Browser GET — unauthenticated in the local single-user edition.
  projectExportUrl: (pid: number) => `/api/projects/${pid}/export.zip`,

  // credentials
  listCredentials: () => request<Credential[]>("/credentials"),
  setCredential: (provider: string, data: Record<string, string>) =>
    request<Credential>("/credentials", { method: "PUT", body: { provider, data } }),

  // zotero
  zoteroStatus: () => request<{ configured: boolean }>("/zotero/status"),
  zoteroCollections: () => request<ZoteroCollection[]>("/zotero/collections"),
  zoteroItems: (collection?: string) =>
    request<ZoteroItem[]>(`/zotero/items${collection ? `?collection=${collection}` : ""}`),
  // Async: returns a Job to poll — each paper syncs with its Summary + code notes + a PDF link.
  zoteroUpload: (project_id: number, paper_ids?: number[]) =>
    request<Job>("/zotero/upload", {
      method: "POST",
      body: { project_id, include_papers: true, paper_ids },
    }),

  // admin: paper sources
  adminSources: () => request<AdminSource[]>("/admin/sources"),
  adminUpdateSource: (id: number, data: AdminSourceWrite) =>
    request<AdminSource>(`/admin/sources/${id}`, { method: "PATCH", body: data }),
  adminCreateSource: (data: AdminSourceWrite) =>
    request<AdminSource>("/admin/sources", { method: "POST", body: data }),
  adminDeleteSource: (id: number) =>
    request<void>(`/admin/sources/${id}`, { method: "DELETE" }),
  // AI Paper Finder conference corpus (admin): list / add (ingest) / delete / toggle.
  adminConferences: () => request<{ venues: PaperfinderVenue[] }>("/admin/conferences"),
  adminAddConference: (venue: string, year: string, source: "openreview" | "cvf") =>
    request<ConferenceIngestTask>("/admin/conferences", { method: "POST", body: { venue, year, source } }),
  adminConferenceIngestStatus: (taskId: string) =>
    request<ConferenceIngestTask>(`/admin/conferences/ingest/${taskId}`),
  adminDeleteConference: (venue: string, year: string) =>
    request<{ deleted: number }>(
      `/admin/conferences/${encodeURIComponent(venue)}/${encodeURIComponent(year)}`,
      { method: "DELETE" },
    ),
  adminToggleConference: (venue: string, year: string, enabled: boolean) =>
    request<PaperfinderVenue>("/admin/conferences", { method: "PATCH", body: { venue, year, enabled } }),
  // Background-worker concurrency (admin-controlled, applied live by the worker supervisor).
  adminWorkers: () => request<WorkersState>("/admin/workers"),
  adminSetWorkers: (worker_concurrency: number) =>
    request<WorkersState>("/admin/workers", { method: "PUT", body: { worker_concurrency } }),
  // Customizable prompts — edited per project (full templates, validated server-side).
  projectPrompts: (pid: number) => request<ProjectPrompt[]>(`/projects/${pid}/prompts`),
  updateProjectPrompts: (pid: number, templates: Record<string, string>) =>
    request<ProjectPrompt[]>(`/projects/${pid}/prompts`, { method: "PUT", body: { templates } }),

  // admin: model catalog
  adminModels: () => request<AdminModel[]>("/admin/models"),
  adminCreateModel: (data: Partial<AdminModel> & { api_key?: string }) =>
    request<AdminModel>("/admin/models", { method: "POST", body: data }),
  adminUpdateModel: (id: number, data: Partial<AdminModel> & { api_key?: string }) =>
    request<AdminModel>(`/admin/models/${id}`, { method: "PATCH", body: data }),
  adminDeleteModel: (id: number) =>
    request<void>(`/admin/models/${id}`, { method: "DELETE" }),
  adminTestModel: (id: number) =>
    request<ApiTestResult>(`/admin/models/${id}/test`, { method: "POST" }),

  // admin: third-party integrations (MinerU)
  adminGetIntegrations: () => request<IntegrationConfig>("/admin/integrations"),
  adminUpdateIntegrations: (
    data: Partial<IntegrationConfig> & { mineru_api_key?: string },
  ) => request<IntegrationConfig>("/admin/integrations", { method: "PUT", body: data }),
  adminTestMineru: () =>
    request<ApiTestResult>("/admin/integrations/mineru/test", { method: "POST" }),
};

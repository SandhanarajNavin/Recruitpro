/**
 * Typed client for the FastAPI backend.
 *
 * The token lives in localStorage rather than a cookie because the API is a separate
 * origin and authenticates with a bearer header. That means it is readable by any
 * script on this origin — acceptable for a recruiter tool behind SSO in production,
 * where you would move to an httpOnly cookie and a same-origin proxy.
 */

import type {
  ActivityItem,
  ApplicationRow,
  JobListItem,
  ChatReply,
  Pipeline,
  Stage,
  Conversation,
  ConversationDetail,
  CandidateDetail,
  CandidateUpdate,
  Interview,
  InterviewList,
  InterviewOutcome,
  InterviewWindow,
  CandidateListResponse,
  Dashboard,
  Job,
  JobDetail,
  JobUpdate,
  Recommendation,
  RankedCandidate,
  Resume,
  ScreeningResultsResponse,
  ScreeningStatusResponse,
  TokenResponse,
  UploadAccepted,
  User,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const TOKEN_KEY = "recruitment.token";
const USER_KEY = "recruitment.user";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isUnauthorized() {
    return this.status === 401;
  }
}

// ── session ───────────────────────────────────────────────────────────────
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    // A corrupt entry should log the user out, not crash the shell.
    return null;
  }
}

function storeSession(token: string, user: User) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

// ── transport ─────────────────────────────────────────────────────────────
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: string }).message;
      if (message) return message;
    }
    // FastAPI validation errors arrive as an array of issues.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: string[] };
      return first.msg ? `${first.loc?.join(".") ?? "request"}: ${first.msg}` : "Invalid request.";
    }
    return response.statusText || "Request failed.";
  } catch {
    return response.statusText || "Request failed.";
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { auth?: boolean } = {},
): Promise<T> {
  const { auth = true, headers, ...rest } = init;
  const merged = new Headers(headers);

  if (auth) {
    const token = getToken();
    if (token) merged.set("Authorization", `Bearer ${token}`);
  }
  if (rest.body && !(rest.body instanceof FormData) && !merged.has("Content-Type")) {
    merged.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...rest, headers: merged });
  } catch {
    // A network-level failure here almost always means the API is not running.
    throw new ApiError(
      `Cannot reach the API at ${API_BASE}. Is the backend running?`,
      0,
    );
  }

  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ── endpoints ─────────────────────────────────────────────────────────────
export const api = {
  async login(email: string, password: string): Promise<User> {
    const result = await request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
      auth: false,
    });
    storeSession(result.access_token, result.user);
    return result.user;
  },

  /** Create a recruiter account. The API signs the new user straight in. */
  async register(name: string, email: string, password: string): Promise<User> {
    const result = await request<TokenResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ name, email, password }),
      auth: false,
    });
    storeSession(result.access_token, result.user);
    return result.user;
  },

  me: () => request<User>("/api/v1/auth/me"),

  // ── assistant ───────────────────────────────────────────────────────────
  /** One turn. Slow by design when the model runs the matching pipeline. */
  chat: (message: string, conversationId?: string) =>
    request<ChatReply>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({
        message,
        conversation_id: conversationId ?? null,
      }),
    }),

  conversations: () => request<Conversation[]>("/api/v1/chat/conversations"),

  // ── hiring pipeline ─────────────────────────────────────────────────────
  pipeline: (jobId?: string) =>
    request<Pipeline>(
      `/api/v1/applications/pipeline${jobId ? `?job_id=${jobId}` : ""}`,
    ),

  applications: (params: { job_id?: string; stage?: Stage } = {}) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) search.set(key, String(value));
    }
    const query = search.toString();
    return request<ApplicationRow[]>(`/api/v1/applications${query ? `?${query}` : ""}`);
  },

  /** Idempotent for a candidate/job pair. */
  openApplication: (body: {
    candidate_id: string;
    job_id: string;
    screening_result_id?: string;
    stage?: Stage;
  }) =>
    request<{ id: string; stage: Stage }>("/api/v1/applications", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Rejected by the server with 409 when the stage machine forbids the move. */
  moveApplication: (id: string, toStage: Stage, note?: string) =>
    request<{ id: string; stage: Stage }>(`/api/v1/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ to_stage: toStage, note: note ?? null }),
    }),

  conversation: (id: string) =>
    request<ConversationDetail>(`/api/v1/chat/conversations/${id}`),

  deleteConversation: (id: string) =>
    request<void>(`/api/v1/chat/conversations/${id}`, { method: "DELETE" }),

  dashboard: (windowDays = 7) =>
    request<Dashboard>(`/api/v1/dashboard?window_days=${windowDays}`),

  /** Notification feed on its own — the bell renders on every page. */
  activity: (limit = 8) => request<ActivityItem[]>(`/api/v1/activity?limit=${limit}`),

  uploadResumes: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request<UploadAccepted>("/api/v1/resumes/upload", {
      method: "POST",
      body: form,
    });
  },

  resumeStatus: (id: string) =>
    request<Pick<Resume, "id" | "status" | "error" | "candidate_id" | "processed_at">>(
      `/api/v1/resumes/${id}/status`,
    ),

  reprocessResume: (id: string) =>
    request<Pick<Resume, "id" | "status" | "error">>(`/api/v1/resumes/${id}/reprocess`, {
      method: "POST",
    }),

  resumeDownloadUrl: (id: string) => `${API_BASE}/api/v1/resumes/${id}/download`,

  candidates: (params: {
    q?: string;
    skill?: string;
    min_years?: number;
    limit?: number;
    offset?: number;
  } = {}) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "" && value !== null) {
        search.set(key, String(value));
      }
    }
    const query = search.toString();
    return request<CandidateListResponse>(
      `/api/v1/candidates${query ? `?${query}` : ""}`,
    );
  },

  interviews: (window: InterviewWindow = "upcoming") =>
    request<InterviewList>(`/api/v1/interviews?window=${window}`),

  scheduleInterview: (body: {
    application_id: string;
    scheduled_at: string;
    kind?: string;
    interviewer?: string;
    /** Also move the application to the interview stage. Defaults on server-side. */
    advance_stage?: boolean;
  }) =>
    request<Interview>("/api/v1/interviews", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateInterview: (
    id: string,
    body: { outcome?: InterviewOutcome; notes?: string; scheduled_at?: string },
  ) =>
    request<Interview>(`/api/v1/interviews/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  cancelInterview: (id: string) =>
    request<Interview>(`/api/v1/interviews/${id}`, { method: "DELETE" }),

  candidate: (id: string) => request<CandidateDetail>(`/api/v1/candidates/${id}`),

  updateCandidate: (id: string, body: CandidateUpdate) =>
    request<CandidateDetail>(`/api/v1/candidates/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  jobs: () => request<JobListItem[]>("/api/v1/jobs"),

  job: (id: string) => request<JobDetail>(`/api/v1/jobs/${id}`),

  updateJob: (id: string, body: JobUpdate) =>
    request<JobDetail>(`/api/v1/jobs/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  createJob: (body: { title?: string; description: string; location?: string }) =>
    request<JobDetail>("/api/v1/jobs", { method: "POST", body: JSON.stringify(body) }),

  // Reads a JD out of a PDF/DOCX/text file. Returns the text rather than a job:
  // extraction is lossy in ways only the recruiter can see, so they edit it first.
  extractJobDescription: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ text: string; filename: string; characters: number }>(
      "/api/v1/jobs/extract",
      { method: "POST", body: form },
    );
  },

  startScreening: (jobId: string) =>
    request<ScreeningStatusResponse>("/api/v1/screenings", {
      method: "POST",
      body: JSON.stringify({ job_id: jobId }),
    }),

  screening: (id: string) => request<ScreeningStatusResponse>(`/api/v1/screenings/${id}`),

  screeningResults: (id: string) =>
    request<ScreeningResultsResponse>(`/api/v1/screenings/${id}/results`),

  override: (screeningId: string, candidateId: string, recommendation: Recommendation, note?: string) =>
    request<RankedCandidate>(
      `/api/v1/screenings/${screeningId}/results/${candidateId}/override`,
      { method: "POST", body: JSON.stringify({ recommendation, note: note ?? null }) },
    ),

  /** Remove a candidate from this job's rankings — now and on every future run. */
  hideFromRankings: (screeningId: string, candidateId: string) =>
    request<void>(`/api/v1/screenings/${screeningId}/results/${candidateId}/hide`, {
      method: "POST",
    }),

  /** Undo a removal. */
  restoreToRankings: (screeningId: string, candidateId: string) =>
    request<void>(`/api/v1/screenings/${screeningId}/results/${candidateId}/hide`, {
      method: "DELETE",
    }),
};

export { API_BASE };

/**
 * Wire contracts, mirroring `backend/app/schemas/api.py`.
 *
 * Hand-maintained rather than generated so the build has no codegen step. The API
 * publishes an OpenAPI schema at /openapi.json — if these drift, generate from that
 * rather than guessing.
 */

export type Recommendation = "strong_hire" | "interview" | "maybe" | "pass";

export type ResumeStatus =
  | "queued"
  | "extracting"
  | "parsing"
  | "embedding"
  | "ready"
  | "failed";

export type ScreeningStatus = "queued" | "running" | "completed" | "failed";

export type ScreeningStage =
  | "queued"
  | "filtering"
  | "retrieving"
  | "reranking"
  | "evaluating"
  | "scoring"
  | "explaining"
  | "done";

export interface User {
  id: string;
  name: string;
  email: string;
  role: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Resume {
  id: string;
  candidate_id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  version: number;
  status: ResumeStatus;
  error: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface RejectedUpload {
  filename: string;
  reason: string;
}

export interface UploadAccepted {
  accepted: Resume[];
  rejected: RejectedUpload[];
}

export interface CandidateProfile {
  id: string;
  current_title: string | null;
  total_years_experience: number;
  seniority_rank: number;
  skills: string[];
  domains: string[];
  education: string[];
  certifications: string[];
  achievements: string[];
  projects: string[];
  experience: Array<Record<string, unknown>>;
  parser_model: string;
  version: number;
}

export interface Candidate {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  location: string | null;
  summary: string | null;
  /** Recruiter's own notes, distinct from the parsed summary. */
  notes: string | null;
  status: string;
  created_at: string;
}

export interface CandidateSummary {
  total: number;
  new_today: number;
  to_review: number;
  shortlisted: number;
}

export interface CandidateListItem extends Candidate {
  current_title: string | null;
  total_years_experience: number;
  skills: string[];
  resume_status: ResumeStatus | null;
  primary_role: string | null;
  /** Best score across every screening. Null when never screened — not zero. */
  best_match_score: number | null;
  /** Furthest pipeline stage reached, across all jobs. */
  stage: Stage | null;
}

export interface CandidateListResponse {
  items: CandidateListItem[];
  total: number;
  limit: number;
  offset: number;
  summary: CandidateSummary;
}

export interface BestMatch {
  score: number;
  recommendation: Recommendation;
  job_id: string;
  job_title: string;
  screening_id: string;
}

export interface CandidateApplication {
  id: string;
  job_id: string;
  job_title: string;
  stage: string;
  created_at: string;
}

/** One job this candidate has been scored against.
 *
 *  `score` is the composite the scoring engine produced for that screening — the
 *  same figure the job page shows, not a fit estimated on this page.
 */
export interface JobMatch {
  job_id: string;
  job_title: string;
  job_status: string;
  department: string | null;
  location: string | null;
  score: number;
  recommendation: Recommendation;
  screening_id: string;
  screened_at: string | null;
  shortlisted: boolean;
  /** Where they placed in that run. */
  rank: number;
  subscores: Subscore[];
  matched_skills: string[];
  missing_skills: string[];
  evidence: EvidenceRecord;
  explanation: Explanation | null;
}

export interface CandidateDetail {
  candidate: Candidate;
  profile: CandidateProfile | null;
  resumes: Resume[];
  /** Null when nobody has screened them — not a score of zero. */
  best_match: BestMatch | null;
  /** Profile skills bucketed for display, already in display order. */
  skill_groups: Record<string, string[]>;
  applications: CandidateApplication[];
  /** Best-fitting jobs, highest score first. */
  job_matches: JobMatch[];
  /** Jobs never screened against this candidate, so the page can say the ranking
   *  covers only runs that actually happened. */
  unscored_jobs: number;
}

export interface CandidateUpdate {
  summary?: string;
  notes?: string;
  location?: string;
  email?: string;
  phone?: string;
  status?: "active" | "archived";
}

export interface RequiredSkill {
  skill: string;
  importance: number;
}

export interface JobRequirement {
  seniority: string | null;
  min_years_experience: number;
  required_skills: RequiredSkill[];
  preferred_skills: string[];
  domains: string[];
  responsibilities: string[];
  education: string[];
  red_flags: string[];
  hard_filters: { must_have_skills?: string[]; min_years?: number };
  parser_model: string;
}

export interface Job {
  id: string;
  title: string;
  description: string;
  location: string | null;
  status: string;
  created_at: string;
  department: string | null;
  employment_type: string | null;
  hiring_manager: string | null;
}

export interface SkillCoverage {
  skill: string;
  /** Candidates in the latest completed screening evidencing this skill. */
  matched: number;
  /** Candidates that screening evaluated — the denominator. */
  of: number;
}

export interface JobStats {
  candidate_count: number;
  strong_match_count: number;
  in_pipeline: number;
  days_open: number;
  new_this_week: number;
  latest_screening_id: string | null;
}

export interface JobDetail {
  job: Job;
  requirement: JobRequirement | null;
  stats: JobStats;
  skill_coverage: SkillCoverage[];
}

export interface JobUpdate {
  title?: string;
  location?: string;
  department?: string;
  employment_type?: string;
  hiring_manager?: string;
  status?: "open" | "on_hold" | "closed";
}

/** Stage widths for one screening run — the funnel, measured. */
export interface Funnel {
  pool: number;
  filtered: number;
  retrieved: number;
  reranked: number;
  evaluated: number;
  shortlisted: number;
}

export interface Screening {
  id: string;
  job_id: string;
  status: ScreeningStatus;
  stage: ScreeningStage;
  mode: "gemini" | "offline";
  weights: Record<string, number>;
  degradations: string[];
  panel_summary: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Subscore {
  category: string;
  label: string;
  score: number;
  weight: number;
  contribution: number;
}

export interface Explanation {
  why_match: string[];
  why_not: string[];
  verdict: string | null;
  model: string;
}

export interface RequirementVerdict {
  requirement: string;
  category: string;
  met: boolean;
  confidence: "high" | "medium" | "low";
  evidence: string[];
  reasoning: string;
}

export interface CategoryAssessment {
  category: string;
  score: number;
  reasoning: string;
  verdicts: RequirementVerdict[];
}

/** Shape of `ScreeningResult.evidence` as written by the screening service. */
export interface EvidenceRecord {
  categories?: CategoryAssessment[];
  strengths?: string[];
  concerns?: string[];
  evaluated_by?: string;
}

export interface RankedCandidate {
  rank: number;
  candidate_id: string;
  name: string;
  /** Identity line under the name. All optional — a candidate still ranks without them. */
  role: string | null;
  years: number | null;
  location: string | null;
  composite_score: number;
  recommendation: Recommendation;
  effective_recommendation: Recommendation;
  shortlisted: boolean;
  subscores: Subscore[];
  matched_skills: string[];
  missing_skills: string[];
  retrieval_score: number | null;
  rerank_score: number | null;
  evidence: EvidenceRecord;
  explanation: Explanation | null;
  override_note: string | null;
}

export interface ScreeningStatusResponse {
  screening: Screening;
  funnel: Funnel;
}

export interface ScreeningResultsResponse {
  screening: Screening;
  funnel: Funnel;
  shortlist: RankedCandidate[];
  also_considered: RankedCandidate[];
}

export interface Metric {
  key: string;
  label: string;
  value: number;
  /** Null when there is no prior window to compare against. */
  delta_pct: number | null;
  /** Line of context under the number — not always a delta. */
  hint: string | null;
  tone: "neutral" | "up" | "down" | "warn";
}

export interface RoleSlice {
  role: string;
  count: number;
  share: number;
}

export interface DayCount {
  day: string;
  count: number;
}

export interface JobListItem extends Job {
  /** Distinct candidates any screening matched to this job. */
  candidate_count: number;
  /** Of those, how many scored in the strong-hire band. */
  strong_match_count: number;
  /** Applications currently open, excluding rejected. */
  in_pipeline: number;
}

export interface JobCard {
  id: string;
  title: string;
  created_at: string;
  match_count: number;
  status: string;
}

export interface RecentCandidate {
  candidate_id: string;
  name: string;
  role: string | null;
  applied_at: string;
  /** Screening verdict where one exists, otherwise the ingestion state. */
  status: string;
}

export interface MatchedCandidate {
  candidate_id: string;
  name: string;
  role: string | null;
  score: number;
  years: number;
}

/** One day of the weekly activity chart. */
export interface DayActivity {
  day: string;
  applied: number;
  hired: number;
}

/** One row of the "Needs Attention" panel. */
export interface AttentionItem {
  job_id: string;
  title: string;
  /** Drives the call to action, so the button label travels with the reason. */
  kind: "review" | "matches" | "find" | "decide";
  detail: string;
  count: number;
}

export interface ActivityItem {
  kind: "resume" | "job" | "screening" | "application";
  text: string;
  at: string;
  href: string | null;
  /** Second line — the job or role the event concerns. */
  context: string | null;
}

export interface ReviewCandidate {
  candidate_id: string;
  name: string;
  email: string | null;
  role: string | null;
  /** Null when never screened — not the same as scoring zero. */
  match_score: number | null;
  years: number;
  status: string;
  /** Best screening verdict, or null when never screened. */
  recommendation: Recommendation | null;
}

export interface OpenJobRow {
  id: string;
  title: string;
  location: string | null;
  candidate_count: number;
  strong_match_count: number;
  days_open: number;
  status: string;
  /** needs_candidates | needs_review | screening, else the stored status. */
  attention_status: string;
}

export interface Dashboard {
  mode: "gemini" | "offline";
  embedding_model: string;
  weights: Record<string, number>;
  funnel_limits: {
    retrieval_limit: number;
    rerank_limit: number;
    shortlist_size: number;
  };
  stats: {
    candidates: number;
    resumes_ready: number;
    resumes_processing: number;
    resumes_failed: number;
  };
  recent_jobs: Job[];
  metrics: Metric[];
  candidates_by_role: RoleSlice[];
  candidates_added: DayCount[];
  job_cards: JobCard[];
  recent_candidates: RecentCandidate[];
  top_matched: MatchedCandidate[];

  attention: AttentionItem[];
  pipeline: Record<string, number>;
  activity: ActivityItem[];
  review_queue: ReviewCandidate[];
  open_jobs: OpenJobRow[];
  /** Percent change per stage over the window; null where there was no baseline. */
  pipeline_deltas: Record<string, number | null>;
  window_days: number;
  weekly_activity: DayActivity[];
}

export const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  strong_hire: "Strong hire",
  interview: "Interview",
  maybe: "Maybe",
  pass: "Pass",
};

/** Terminal resume states — used to decide when to stop polling. */
export const RESUME_TERMINAL: ResumeStatus[] = ["ready", "failed"];
export const SCREENING_TERMINAL: ScreeningStatus[] = ["completed", "failed"];

// ── assistant ─────────────────────────────────────────────────────────────
export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  is_write: boolean;
}

export interface ChatReply {
  conversation_id: string;
  message: string;
  tool_calls: ToolCall[];
  engine: string;
}

export interface ChatMessageRow {
  id: string;
  sequence: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: ChatMessageRow[];
}

// ── hiring pipeline ───────────────────────────────────────────────────────
export type Stage =
  | "applied"
  | "screening"
  | "shortlisted"
  | "interview"
  | "offer"
  | "hired"
  | "rejected";

/** Legal stage moves, mirroring `ALLOWED` in `app/services/application_service.py`.
 *
 *  Duplicated so a stage picker can offer only moves that will succeed, rather than
 *  letting the recruiter choose one and receive a 409. The server stays the
 *  authority: it rejects anything not in its own table, and callers here surface
 *  that error rather than assuming this copy is right.
 */
export const STAGE_MOVES: Record<Stage, Stage[]> = {
  applied: ["screening", "rejected"],
  screening: ["shortlisted", "rejected"],
  shortlisted: ["interview", "rejected"],
  interview: ["offer", "rejected"],
  offer: ["hired", "rejected"],
  // A hire is terminal — and it also takes the candidate out of every ranking.
  hired: [],
  // Undoing a rejection returns them to the shortlist.
  rejected: ["shortlisted"],
};

export const STAGE_LABELS: Record<Stage, string> = {
  applied: "Applied",
  screening: "Screening",
  shortlisted: "Shortlisted",
  interview: "Interview",
  offer: "Offer",
  hired: "Hired",
  rejected: "Rejected",
};

export interface ApplicationRow {
  id: string;
  candidate_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string;
  stage: Stage;
  match_score_at_entry: number | null;
  created_at: string;
}

export interface Pipeline {
  stages: Record<string, number>;
  total: number;
}

/* ── interviews ───────────────────────────────────────────────────── */

export type InterviewOutcome = "scheduled" | "completed" | "cancelled" | "no_show";

export interface Interview {
  id: string;
  application_id: string;
  candidate_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string;
  scheduled_at: string;
  kind: string | null;
  interviewer: string | null;
  outcome: InterviewOutcome;
  notes: string | null;
  /** The application's pipeline stage, which the outcome does not imply. */
  stage: string;
}

export interface InterviewList {
  items: Interview[];
  counts: Record<string, number>;
}

export type InterviewWindow = "week" | "upcoming" | "past" | "all";

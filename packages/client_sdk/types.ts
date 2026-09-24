export type Surface = "cli";

export interface ClientCommand {
  command_id: string;
  client_id: string;
  surface: Surface;
  session_id: string | null;
  learner_id: string;
  type: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface CommandAccepted {
  command_id: string;
  session_id: string | null;
  status: string;
  trace_id?: string | null;
  result?: Record<string, unknown> | null;
}

export type ResourceType = "textbook" | "lecture" | "paper" | "quiz_bank" | "student_upload";
export type ResourceStatus = "draft" | "active" | "archived";

export interface ResourceRecord {
  resource_id: string;
  document_id: string;
  course_id: string | null;
  type: ResourceType;
  title: string;
  tags: string[];
  source_type: "import" | "upload";
  source_url: string;
  license: string;
  hash: string;
  status: ResourceStatus;
  owner_id: string;
  visibility: "private" | "public";
  version_of: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface RuntimeEvent {
  event_id: string;
  session_id: string;
  trace_id: string;
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  source: string;
  timestamp: string;
  client_id?: string | null;
  surface?: Surface | null;
  audience?: string | null;
}

export interface SessionSummary {
  session_id: string;
  learner_id: string;
  title: string;
  parent_id: string | null;
  created_at: string;
  updated_at: string;
  last_sequence: number;
  message_count: number;
  session_mode?: "chat" | "study";
  experiment_group?: ExperimentGroup | "legacy";
  course_id?: string;
  provider_profile?: string;
  model?: string;
}

export interface SessionMessage {
  index: number;
  role: string;
  content: string;
  name?: string | null;
  metadata?: Record<string, unknown>;
}

export type ExperimentGroup = "A" | "B" | "C";

export interface AttemptRecord {
  attempt_id: string;
  learner_id: string;
  session_id: string;
  trace_id: string;
  course_id: string;
  item_id: string;
  scored_concept_id: string;
  concept_ids: string[];
  question_bank_version: string;
  correct: boolean | null;
  timestamp: string;
  hint_count: number;
  grading_source: string;
  confidence: number;
}

export interface LearnerEstimate {
  status: "available" | "insufficient_data" | "group_disabled";
  learner_id?: string;
  course_id?: string;
  concept_id?: string;
  model_type?: "bkt";
  model_version?: string;
  config_hash?: string;
  mastery: number | null;
  evidence_count: number;
  uncertainty: number | null;
  uncertainty_kind?: "binary_entropy_bits; not a confidence interval" | null;
  updated_at?: string;
  parameter_status?: string;
}

export interface DocumentConversionResult {
  status: "ok" | "error";
  source_sha256?: string;
  page_count?: number;
  ocr_used?: boolean;
  review_required?: boolean;
  markdown_path?: string;
  metadata_path?: string;
  format?: string;
  markitdown_version?: string;
  error_code?: string;
}

export interface CourseSummary {
  course_id: string;
  title: string;
  concept_count: number;
  configured: boolean;
  imported: boolean;
  resource_id: string | null;
  content_hash: string | null;
  concepts: Array<Record<string, unknown>>;
  source: Record<string, unknown>;
}

export interface ProviderSelection {
  role: string;
  profile_id: string;
  model: string;
}

export interface ProviderProfile {
  profile_id: string;
  display_name: string;
  protocol: string;
  base_url: string;
  api_key_ref: string;
  default_model: string;
  models: string[];
  api_mode: string;
  timeout_ms: number;
  max_retries: number;
  capabilities: Record<string, boolean>;
  enabled: boolean;
  has_secret: boolean;
}

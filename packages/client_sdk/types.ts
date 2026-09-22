export type Surface = "desktop" | "cli" | "pet";

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
  source_type: "import" | "upload" | "crawl";
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
}

export interface SessionMessage {
  index: number;
  role: string;
  content: string;
  name?: string | null;
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

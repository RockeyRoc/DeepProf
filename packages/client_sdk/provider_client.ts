import { parseResponse } from "./command_bus.js";
import type { ProviderProfile, ProviderSelection } from "./types.js";

export interface ProviderProfileInput {
  profile_id: string;
  display_name: string;
  protocol?: "openai_compatible" | "native" | "local";
  base_url: string;
  api_key?: string;
  api_key_ref?: string;
  default_model: string;
  models?: string[];
  api_mode?: "chat_completions" | "responses" | "auto";
  extra_headers?: Record<string, string>;
  timeout_ms?: number;
  max_retries?: number;
  capabilities?: Record<string, boolean>;
  enabled?: boolean;
}

export class ProviderClient {
  constructor(private readonly baseUrl: string) {}

  async list(): Promise<ProviderProfile[]> {
    const response = await fetch(`${this.baseUrl}/providers`);
    return parseResponse<ProviderProfile[]>(response, "providers_failed");
  }

  async upsert(profile: ProviderProfileInput): Promise<ProviderProfile> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profile.profile_id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(profile),
    });
    return parseResponse<ProviderProfile>(response, "provider_save_failed");
  }

  async models(profileId: string): Promise<string[]> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profileId)}/models`);
    return parseResponse<string[]>(response, "models_failed");
  }

  async discoverModels(profileId: string): Promise<string[]> {
    return this.models(profileId);
  }

  async probe(profileId: string, model?: string): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profileId)}/probe`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: model || null }),
    });
    return parseResponse<Record<string, unknown>>(response, "probe_failed");
  }

  async defaultSelection(role = "tutor.default"): Promise<ProviderSelection> {
    const response = await fetch(`${this.baseUrl}/providers/default?role=${encodeURIComponent(role)}`);
    return parseResponse<ProviderSelection>(response, "provider_default_failed");
  }

  async setDefault(profileId: string, model = "", role = "tutor.default"): Promise<ProviderSelection> {
    const response = await fetch(`${this.baseUrl}/providers/default`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role, profile_id: profileId, model }),
    });
    return parseResponse<ProviderSelection>(response, "provider_default_failed");
  }
}

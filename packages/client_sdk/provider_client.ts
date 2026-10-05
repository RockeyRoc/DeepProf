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
  vendor_id?: string;
  model_selection_mode?: "catalog" | "manual";
  model_capabilities?: Record<string, Record<string, unknown>>;
  enabled?: boolean;
}

export class ProviderClient {
  constructor(private readonly baseUrl: string) {}

  async webSearchStatus(profileId = "", model = ""): Promise<Record<string, unknown>> {
    const query = `?profile_id=${encodeURIComponent(profileId)}&model=${encodeURIComponent(model)}`;
    const response = await fetch(`${this.baseUrl}/settings/web-search${query}`);
    return parseResponse<Record<string, unknown>>(response, "web_search_settings_failed");
  }

  async probeWebSearch(profileId = "", model = ""): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/settings/web-search/probe`, { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ profile_id: profileId, model }) });
    return parseResponse<Record<string, unknown>>(response, "web_search_probe_failed");
  }

  async saveWebSearchCredential(profileId: string, model: string, input: { api_key?: string; clear_key?: boolean }): Promise<Record<string, unknown>> {
    const query = `?profile_id=${encodeURIComponent(profileId)}&model=${encodeURIComponent(model)}`;
    const response = await fetch(`${this.baseUrl}/settings/web-search${query}`, { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(input) });
    return parseResponse<Record<string, unknown>>(response, "web_search_credential_save_failed");
  }

  async list(): Promise<ProviderProfile[]> {
    const response = await fetch(`${this.baseUrl}/providers`);
    return parseResponse<ProviderProfile[]>(response, "providers_failed");
  }

  async catalog(query = ""): Promise<Array<Record<string, unknown>>> {
    const response = await fetch(`${this.baseUrl}/providers/catalog?query=${encodeURIComponent(query)}`);
    return parseResponse<Array<Record<string, unknown>>>(response, "provider_catalog_failed");
  }

  async discover(input: ProviderProfileInput & { vendor_id?: string }): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/providers/discover`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input),
    });
    return parseResponse<Record<string, unknown>>(response, "model_discovery_failed");
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

  async modelCatalog(profileId: string): Promise<Array<Record<string, unknown>>> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profileId)}/model-catalog`);
    return parseResponse<Array<Record<string, unknown>>>(response, "model_catalog_failed");
  }

  async updateModels(profileId: string, models: string[], defaultModel: string, modelCapabilities: Record<string, Record<string, unknown>> = {}): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profileId)}/models`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ models, default_model: defaultModel, model_capabilities: modelCapabilities }),
    });
    return parseResponse<Record<string, unknown>>(response, "models_save_failed");
  }

  async deleteModel(profileId: string, model: string, replacementModel = "", replacementProfile = ""): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profileId)}/models/${encodeURIComponent(model)}`, {
      method: "DELETE", headers: { "content-type": "application/json" },
      body: JSON.stringify({ replacement_model: replacementModel, replacement_profile_id: replacementProfile }),
    });
    return parseResponse<Record<string, unknown>>(response, "model_delete_failed");
  }

  async deleteProfile(profileId: string, replacementProfile = "", replacementModel = ""): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/providers/${encodeURIComponent(profileId)}`, {
      method: "DELETE", headers: { "content-type": "application/json" },
      body: JSON.stringify({ replacement_profile_id: replacementProfile, replacement_model: replacementModel }),
    });
    return parseResponse<Record<string, unknown>>(response, "provider_delete_failed");
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
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role, profile_id: profileId, model }),
    });
    return parseResponse<ProviderSelection>(response, "provider_default_failed");
  }
}

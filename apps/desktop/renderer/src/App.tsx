import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { CommandBus } from "../../../../packages/client_sdk/command_bus";
import { EventClient } from "../../../../packages/client_sdk/event_client";
import { initialProjectedState, projectEvent, type ProjectedState } from "../../../../packages/client_sdk/event_projector";
import { SessionClient } from "../../../../packages/client_sdk/session_client";
import type { ProviderProfile, RuntimeEvent, SessionMessage, SessionSummary } from "../../../../packages/client_sdk/types";
import { useLocale, type Translator } from "./i18n";

type Bootstrap = { runtime: { state: string; baseUrl: string | null }; workbenchUrl: string | null };

type ProviderDraft = {
  profile_id: string;
  display_name: string;
  protocol: "openai_compatible";
  base_url: string;
  default_model: string;
  models: string[];
  api_mode: "auto";
  extra_headers: Record<string, string>;
  timeout_ms: number;
  max_retries: number;
  capabilities: Record<string, boolean>;
  enabled: boolean;
};

const emptyProfile: ProviderDraft = {
  profile_id: "default",
  display_name: "My provider",
  protocol: "openai_compatible",
  base_url: "https://api.openai.com/v1",
  default_model: "",
  models: [],
  api_mode: "auto",
  extra_headers: {},
  timeout_ms: 120000,
  max_retries: 2,
  capabilities: {},
  enabled: true,
};

const ACTIVE_SESSION_KEY = "deepprof.activeSessionId:v1";

function toDraft(profile?: ProviderProfile): ProviderDraft {
  if (!profile) return { ...emptyProfile };
  return {
    profile_id: profile.profile_id,
    display_name: profile.display_name,
    protocol: "openai_compatible",
    base_url: profile.base_url,
    default_model: profile.default_model,
    models: [...profile.models],
    api_mode: "auto",
    extra_headers: {},
    timeout_ms: profile.timeout_ms,
    max_retries: profile.max_retries,
    capabilities: { ...profile.capabilities },
    enabled: profile.enabled,
  };
}

export default function App() {
  const { locale, setLocale, t } = useLocale();
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [projection, setProjection] = useState<ProjectedState>(initialProjectedState);
  const [transcript, setTranscript] = useState<SessionMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [onboarding, setOnboarding] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profile, setProfile] = useState<ProviderDraft>({ ...emptyProfile });
  const [secret, setSecret] = useState("");

  const runtimeUrl = bootstrap?.runtime.baseUrl || "";
  const commands = useMemo(() => runtimeUrl ? new CommandBus(runtimeUrl, "desktop", "desktop") : null, [runtimeUrl]);
  const sessionClient = useMemo(() => commands ? new SessionClient(runtimeUrl, commands) : null, [commands, runtimeUrl]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const value = await window.deepprof?.bootstrap();
        if (!active) return;
        if (!value) throw new Error("desktop_api_unavailable");
        setBootstrap(value);
        if (!value.runtime.baseUrl) throw new Error("runtime_unavailable");
        const [providerResponse, sessionResponse] = await Promise.all([
          fetch(`${value.runtime.baseUrl}/providers`),
          fetch(`${value.runtime.baseUrl}/sessions?learner_id=local`),
        ]);
        if (!active) return;
        const providerList = providerResponse.ok ? await providerResponse.json() as ProviderProfile[] : [];
        setProfiles(providerList);
        setOnboarding(providerList.every((profile) => profile.profile_id === "mock"));
        if (sessionResponse.ok) {
          const loaded = await sessionResponse.json() as SessionSummary[];
          setSessions(loaded);
          try {
            const saved = localStorage.getItem(ACTIVE_SESSION_KEY) || localStorage.getItem("deepprof.activeSessionId");
            if (saved && loaded.some((item) => item.session_id === saved)) setSessionId(saved);
          } catch { /* private browsing may disable localStorage */ }
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Startup failed");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!sessionId || !runtimeUrl) return;
    let cancelled = false;
    const client = new EventClient({
      baseUrl: runtimeUrl,
      onEvent: (event: RuntimeEvent) => setProjection((current) => projectEvent(current, event)),
      onError: () => setError("Event stream interrupted; reconnecting"),
    });
    const load = async () => {
      try {
        const summary = sessions.find((item) => item.session_id === sessionId) || await sessionClient?.get(sessionId);
        const messages = await sessionClient?.transcript(sessionId);
        if (cancelled) return;
        setTranscript(messages || []);
        setProjection(initialProjectedState);
        client.connect(sessionId, summary?.last_sequence || 0);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Session load failed");
      }
    };
    void load();
    return () => { cancelled = true; client.close(); };
  }, [sessionId, runtimeUrl, sessions, sessionClient]);

  async function saveProvider(next: ProviderDraft, credential: string): Promise<void> {
    if (!window.deepprof) return;
    setError("");
    const saved = await window.deepprof.provider.configure(next, credential) as unknown as ProviderProfile;
    setProfiles((current) => [...current.filter((item) => item.profile_id !== saved.profile_id), saved]);
    setProfile(toDraft(saved));
    setSecret("");
    setOnboarding(false);
  }

  async function completeOnboarding(event: FormEvent): Promise<void> {
    event.preventDefault();
    try {
      await saveProvider(profile, secret);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Provider connection failed");
    }
  }

  async function createSession(): Promise<void> {
    if (!sessionClient) return;
    try {
      const created = await sessionClient.create(t("newSession"));
      setSessionId(created);
      setTranscript([]);
      setProjection(initialProjectedState);
      try { localStorage.setItem(ACTIVE_SESSION_KEY, created); } catch { /* optional client preference */ }
      setSessions(await sessionClient.list());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create session");
    }
  }

  async function sendMessage(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!sessionClient || !input.trim()) return;
    const content = input.trim();
    setInput("");
    try {
      let current = sessionId;
      if (!current) {
        current = await sessionClient.create(t("newSession"));
        setSessionId(current);
      }
      try { localStorage.setItem(ACTIVE_SESSION_KEY, current); } catch { /* optional client preference */ }
      await sessionClient.send(current, content);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Message failed");
    }
  }

  if (loading) return <div className="loading-screen"><div className="brand-mark">DP</div><p>{t("startup")}</p></div>;
  if (onboarding) return <Onboarding profile={profile} secret={secret} error={error} onProfile={setProfile} onSecret={setSecret} onSubmit={completeOnboarding} t={t} />;

  const activeProvider = profiles.find((profile) => profile.profile_id !== "mock") || profiles[0];
  const selectSession = async (id: string) => {
    if (!sessionClient) return;
    try {
      await sessionClient.resume(id);
      setSessionId(id);
      try { localStorage.setItem(ACTIVE_SESSION_KEY, id); } catch { /* optional client preference */ }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not resume session");
    }
  };
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">DP</span><span>DeepProf</span></div>
        <div className="course">{t("workspace")} <span>/</span> {t("course")}</div>
        <div className="topbar-right"><span className={`status-dot ${bootstrap?.runtime.state === "READY" ? "ready" : "warning"}`} />{activeProvider?.display_name || t("offline")}<button className="icon-button" aria-label={t("settings")} onClick={() => setSettingsOpen(true)}>⚙</button><button className="language-button" onClick={() => setLocale(locale === "en" ? "zh" : "en")} aria-label="Switch language">{t("language")}</button></div>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <Section title={t("workspace")}><NavItem icon="●" label={t("overview")} active /><NavItem icon="◌" label={t("sessions")} badge={String(sessions.length)} /><NavItem icon="◌" label={t("courses")} /></Section>
          <div className="session-list" aria-label={t("sessions")}>
            {sessions.slice(0, 8).map((item) => <button key={item.session_id} className={item.session_id === sessionId ? "session-list-item active" : "session-list-item"} onClick={() => void selectSession(item.session_id)}><strong>{item.title || t("newSession")}</strong><small>{item.session_id.slice(0, 10)}</small></button>)}
          </div>
          <Section title={t("learning")}><NavItem icon="◆" label={t("library")} /><NavItem icon="✓" label={t("review")} /><NavItem icon="◌" label={t("progress")} /></Section>
          <Section title={t("system")}><NavItem icon="◌" label={t("providers")} onClick={() => setSettingsOpen(true)} /><NavItem icon="⚙" label={t("settings")} onClick={() => setSettingsOpen(true)} /></Section>
          <button className="new-session" onClick={() => void createSession()}>＋ {t("newSession")}</button>
        </aside>
        <main className="main-panel">
          <div className="workspace-heading"><div><p className="eyebrow">{t("currentSession")}</p><h1>{sessionId ? t("conceptPractice") : t("startLearning")}</h1></div><span className="session-chip">{sessionId ? sessionId.slice(0, 16) : t("noSession")}</span></div>
          <div className="chat-card">
            {!sessionId && <div className="empty-chat"><div className="empty-orbit">✦</div><h2>{t("whatLearn")}</h2><p>{t("learningPrompt")}</p><div className="suggestions"><button onClick={() => setInput("Help me understand this concept")}>{t("explainConcept")}</button><button onClick={() => setInput("Give me a practice question")}>{t("practiceTest")}</button><button onClick={() => setInput("Help me review my notes")}>{t("reviewNotes")}</button></div></div>}
            {sessionId && <div className="conversation"><div className="message system-message"><span className="avatar">DP</span><div><p>{t("connected")}</p><small>DeepProf · {t("teachingAction")}: {projection.activeAction || "assess"}</small></div></div>{transcript.map((message) => <div className="message" key={`${message.index}-${message.role}`}><span className={message.role === "user" ? "avatar you" : "avatar"}>{message.role === "user" ? "You" : "DP"}</span><div><p>{message.content}</p></div></div>)}{projection.answer && !transcript.some((message) => message.content === projection.answer) && <div className="message"><span className="avatar">DP</span><div><p>{projection.answer}</p><small>{t("roundStatus")}: {projection.status}</small></div></div>}</div>}
            <form className="composer" onSubmit={sendMessage}><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={t("askPlaceholder")} rows={2} /><div className="composer-footer"><span>{t("enterSend")}</span><button type="submit" className="send-button" disabled={!input.trim()}>{t("send")}</button></div></form>
          </div>
          <div className="bottom-tabs"><button className="tab active">Sources</button><button className="tab">Session Tree</button><button className="tab">Trace <span className="tab-count">{projection.events.length}</span></button><span className="bottom-hint">RuntimeEvent sequence: {projection.lastSequence}</span></div>
        </main>
        <aside className="context-panel"><div className="panel-title"><span>{t("context")}</span><span className="more">···</span></div><ContextRow label={t("learnerState")} value={t("evidenceBuilding")} /><ContextRow label={t("currentConcept")} value={t("notSelected")} /><ContextRow label={t("learningGoal")} value={t("understandApply")} /><div className="context-divider" /><div className="panel-title"><span>{t("teachingSignal")}</span><span className="signal-pill">{projection.status}</span></div><div className="signal-card"><div className="signal-icon">✦</div><div><strong>{projection.activeAction || "Assess"}</strong><p>{t("everyDecision")}</p></div></div><div className="context-divider" /><div className="panel-title"><span>{t("pet")}</span><span className="pet-state">{projection.status}</span></div><div className="pet-card"><div className="pet-orb">●</div><div><strong>DeepProf companion</strong><p>{t("petDriven")}</p></div><button aria-label={t("openPet")} onClick={() => void window.deepprof?.runtimeStatus()}>→</button></div>{error && <div className="error-banner">{error}</div>}</aside>
      </div>
      {settingsOpen && <ProviderSettings runtimeUrl={runtimeUrl} profiles={profiles.filter((profile) => profile.profile_id !== "mock")} onClose={() => setSettingsOpen(false)} onSaved={(saved) => setProfiles((current) => [...current.filter((item) => item.profile_id !== saved.profile_id), saved])} t={t} />}
    </div>
  );
}

function Onboarding({ profile, secret, error, onProfile, onSecret, onSubmit, t }: { profile: ProviderDraft; secret: string; error: string; onProfile: (value: ProviderDraft) => void; onSecret: (value: string) => void; onSubmit: (event: FormEvent) => void; t: Translator }) {
  return <div className="onboarding"><div className="onboarding-card"><div className="brand"><span className="brand-mark">DP</span><span>DeepProf</span></div><p className="eyebrow">{t("firstRun")}</p><h1>{t("connectModel")}</h1><p className="lead">{t("noAccount")}</p><form onSubmit={onSubmit} className="onboarding-form"><ProviderFields profile={profile} onChange={onProfile} t={t} /><label>{t("credential")}<input type="password" value={secret} onChange={(event) => onSecret(event.target.value)} autoComplete="off" required /></label>{error && <div className="error-banner">{error}</div>}<button className="primary-button" type="submit">{t("saveTest")}</button></form><p className="privacy-note">{t("privacy")}</p></div></div>;
}

function ProviderSettings({ runtimeUrl, profiles, onClose, onSaved, t }: { runtimeUrl: string; profiles: ProviderProfile[]; onClose: () => void; onSaved: (profile: ProviderProfile) => void; t: Translator }) {
  const [selectedId, setSelectedId] = useState(profiles[0]?.profile_id || "new");
  const [draft, setDraft] = useState<ProviderDraft>(toDraft(profiles[0]));
  const [credential, setCredential] = useState("");
  const [models, setModels] = useState<string[]>(profiles[0]?.models || []);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const selected = profiles.find((profile) => profile.profile_id === selectedId);
    const next = selected ? toDraft(selected) : { ...emptyProfile, profile_id: `provider-${profiles.length + 1}` };
    setDraft(next);
    setModels(selected?.models || []);
    setCredential("");
    setNotice("");
  }, [profiles, selectedId]);

  async function save(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!window.deepprof) return;
    setBusy(true);
    setNotice("");
    try {
      const saved = await window.deepprof.provider.configure({ ...draft, models }, credential) as unknown as ProviderProfile;
      onSaved(saved);
      setSelectedId(saved.profile_id);
      setCredential("");
      setNotice(t("savedProbe"));
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Provider save failed");
    } finally {
      setBusy(false);
    }
  }

  async function probe(): Promise<void> {
    if (!runtimeUrl) return;
    setBusy(true);
    try {
      const response = await fetch(`${runtimeUrl}/providers/${encodeURIComponent(draft.profile_id)}/probe`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model: draft.default_model || null }) });
      const result = await response.json() as { status?: string; kind?: string; message?: string };
      setNotice(`${result.status || (response.ok ? "ok" : "failed")}${result.kind ? ` · ${result.kind}` : ""}${result.message ? ` · ${result.message}` : ""}`);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Probe failed");
    } finally {
      setBusy(false);
    }
  }

  async function discoverModels(): Promise<void> {
    if (!runtimeUrl) return;
    setBusy(true);
    try {
      const response = await fetch(`${runtimeUrl}/providers/${encodeURIComponent(draft.profile_id)}/models`);
      if (!response.ok) throw new Error(`model_discovery_failed:${response.status}`);
      const discovered = await response.json() as string[];
      setModels(discovered);
      setDraft((current) => ({ ...current, models: discovered, default_model: current.default_model || discovered[0] || "" }));
      setNotice(`${discovered.length} ${t("modelsFound")}`);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Model discovery failed");
    } finally {
      setBusy(false);
    }
  }

  return <div className="settings-backdrop" role="presentation"><section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="settings-heading"><div><p className="eyebrow">{t("providerSettings")}</p><h2 id="settings-title">{t("modelConnections")}</h2></div><button className="icon-button" onClick={onClose} aria-label={t("closeSettings")}>×</button></div><div className="settings-layout"><nav className="provider-list"><button className={selectedId === "new" ? "provider-list-item selected" : "provider-list-item"} onClick={() => setSelectedId("new")}>{t("addProvider")}</button>{profiles.map((item) => <button key={item.profile_id} className={selectedId === item.profile_id ? "provider-list-item selected" : "provider-list-item"} onClick={() => setSelectedId(item.profile_id)}><strong>{item.display_name || item.profile_id}</strong><small>{item.has_secret ? t("credentialSaved") : t("credentialMissing")}</small></button>)}</nav><form className="settings-form" onSubmit={save}><ProviderFields profile={draft} onChange={setDraft} t={t} /><label>{t("credential")}<input type="password" value={credential} onChange={(event) => setCredential(event.target.value)} autoComplete="off" placeholder="Required when saving" required /></label><div className="model-tools"><button type="button" className="secondary-button" onClick={() => void discoverModels()} disabled={busy}>{t("discoverModels")}</button><button type="button" className="secondary-button" onClick={() => void probe()} disabled={busy}>{t("testHealth")}</button></div>{models.length > 0 && <div className="model-list"><span>{t("availableModels")}</span>{models.map((model) => <button type="button" key={model} onClick={() => setDraft((current) => ({ ...current, default_model: model }))}>{model}</button>)}</div>}{notice && <div className="settings-notice">{notice}</div>}<div className="settings-actions"><button type="button" className="secondary-button" onClick={onClose}>{t("cancel")}</button><button className="primary-button" type="submit" disabled={busy}>{busy ? t("working") : t("saveProvider")}</button></div></form></div></section></div>;
}

function ProviderFields({ profile, onChange, t }: { profile: ProviderDraft; onChange: (value: ProviderDraft) => void; t: Translator }) {
  return <><label>{t("profileId")}<input value={profile.profile_id} onChange={(event) => onChange({ ...profile, profile_id: event.target.value })} required /></label><label>{t("displayName")}<input value={profile.display_name} onChange={(event) => onChange({ ...profile, display_name: event.target.value })} required /></label><label>{t("baseUrl")}<input type="url" value={profile.base_url} onChange={(event) => onChange({ ...profile, base_url: event.target.value })} required /></label><label>{t("defaultModel")}<input value={profile.default_model} onChange={(event) => onChange({ ...profile, default_model: event.target.value })} placeholder="For example, gpt-4o-mini" required /></label></>;
}

function Section({ title, children }: { title: string; children: ReactNode }) { return <section className="nav-section"><p>{title}</p>{children}</section>; }
function NavItem({ icon, label, badge, active, onClick }: { icon: string; label: string; badge?: string; active?: boolean; onClick?: () => void }) { return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}><span>{icon}</span>{label}{badge && <small>{badge}</small>}</button>; }
function ContextRow({ label, value }: { label: string; value: string }) { return <div className="context-row"><span>{label}</span><strong>{value}</strong></div>; }

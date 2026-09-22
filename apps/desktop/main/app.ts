import { app, BrowserWindow, ipcMain } from "electron";
import { BackendBroker } from "./backend_broker";
import { assertText, assertTrustedRenderer } from "./ipc_guard";
import { SecretStore } from "./secrets";
import { RuntimeSupervisor } from "./runtime_supervisor";
import { createPetWindow } from "./pet_window";
import { createTray } from "./tray";
import { createMainWindow, installStrictCsp } from "./windows";

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) app.quit();

let mainWindow: BrowserWindow | null = null;
let petWindow: BrowserWindow | null = null;
let tray: ReturnType<typeof createTray> | null = null;
let runtime: RuntimeSupervisor;
let broker: BackendBroker;
let secrets: SecretStore;
let petClickThrough = false;

function setPetClickThrough(ignore: boolean): void {
  petClickThrough = ignore;
  petWindow?.setIgnoreMouseEvents(ignore, { forward: true });
}

async function hydrateRuntimeSecrets(): Promise<void> {
  const baseUrl = runtime.status.baseUrl;
  if (!baseUrl) return;
  const response = await fetch(`${baseUrl}/providers`);
  if (!response.ok) return;
  const profiles = await response.json() as Array<Record<string, unknown>>;
  await Promise.all(profiles.map(async (profile) => {
    const profileId = typeof profile.profile_id === "string" ? profile.profile_id : "";
    const ref = typeof profile.api_key_ref === "string" && profile.api_key_ref ? profile.api_key_ref : `provider:${profileId}`;
    const value = profileId ? secrets.get(ref) : null;
    if (!profileId || !value) return;
    await fetch(`${baseUrl}/providers/${encodeURIComponent(profileId)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...profile, profile_id: profileId, api_key_ref: ref, api_key: value }),
    });
  }));
}

function registerIpc(): void {
  ipcMain.handle("app.bootstrap", (event) => {
    assertTrustedRenderer(event, mainWindow as BrowserWindow);
    return { runtime: runtime.status, workbenchUrl: broker.url };
  });

  ipcMain.handle("runtime.status", (event) => {
    assertTrustedRenderer(event, mainWindow as BrowserWindow);
    return runtime.status;
  });

  ipcMain.handle("pet.bootstrap", (event) => {
    assertTrustedRenderer(event, [mainWindow as BrowserWindow, petWindow as BrowserWindow]);
    return { runtime: runtime.status };
  });

  ipcMain.handle("pet.interact", async (event, input: { kind: unknown }) => {
    assertTrustedRenderer(event, [mainWindow as BrowserWindow, petWindow as BrowserWindow]);
    const kind = assertText(input?.kind, "interaction");
    if (kind === "open_workspace") {
      mainWindow?.show();
      return { accepted: true };
    }
    const baseUrl = runtime.status.baseUrl;
    if (!baseUrl) throw new Error("runtime_unavailable");
    const response = await fetch(`${baseUrl}/commands`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ command_id: `pet_${crypto.randomUUID()}`, client_id: "pet", surface: "pet", session_id: null, learner_id: "local", type: "pet.interact", payload: { kind }, timestamp: new Date().toISOString() }),
    });
    return { accepted: response.ok };
  });

  ipcMain.handle("pet.set-ignore-mouse-events", (event, input: { ignore: unknown }) => {
    assertTrustedRenderer(event, [mainWindow as BrowserWindow, petWindow as BrowserWindow]);
    if (typeof input?.ignore !== "boolean") throw new Error("invalid_ignore_mouse_events");
    setPetClickThrough(input.ignore);
    return { ignore: petClickThrough };
  });

  ipcMain.handle("provider.configure", async (event, input: { profile: Record<string, unknown>; secret: unknown }) => {
    assertTrustedRenderer(event, mainWindow as BrowserWindow);
    const profileId = assertText(input?.profile?.profile_id, "profile_id");
    const ref = `provider:${profileId}`;
    const value = assertText(input?.secret, "secret_value");
    const baseUrl = runtime.status.baseUrl;
    if (!baseUrl) throw new Error("runtime_unavailable");
    secrets.set(ref, value);
    const response = await fetch(`${baseUrl}/providers/${encodeURIComponent(profileId)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...input.profile, profile_id: profileId, api_key_ref: ref, api_key: value }),
    });
    if (!response.ok) throw new Error(`provider_save_failed:${response.status}`);
    return response.json();
  });
}

async function start(): Promise<void> {
  if (!singleInstance) return;
  app.setAppUserModelId("cn.edu.xtu.deepprof.desktop");
  installStrictCsp();
  runtime = new RuntimeSupervisor();
  broker = new BackendBroker();
  secrets = new SecretStore();
  registerIpc();
  await runtime.start();
  await hydrateRuntimeSecrets();
  const url = await broker.start();
  mainWindow = createMainWindow(url);
  petWindow = createPetWindow(url);
  tray = createTray(
    () => mainWindow?.show(),
    () => setPetClickThrough(!petClickThrough),
    () => app.quit(),
  );
}

app.on("second-instance", () => mainWindow?.show());
app.whenReady().then(start).catch((error) => {
  console.error("DeepProf failed to start", error instanceof Error ? error.message : error);
  app.quit();
});
app.on("before-quit", () => {
  runtime?.stop();
  broker?.stop();
  petWindow?.close();
  tray?.destroy();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

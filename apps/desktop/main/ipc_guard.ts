import type { IpcMainInvokeEvent, BrowserWindow } from "electron";

export function assertTrustedRenderer(event: IpcMainInvokeEvent, window: BrowserWindow | BrowserWindow[]): void {
  const windows = Array.isArray(window) ? window : [window];
  if (!windows.some((candidate) => candidate && !candidate.isDestroyed() && candidate.webContents.id === event.sender.id)) throw new Error("untrusted_sender");
  const url = new URL(event.senderFrame?.url || "http://invalid/");
  if (url.hostname !== "127.0.0.1") throw new Error("renderer_must_be_loopback");
}

export function assertText(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 4096) {
    throw new Error(`invalid_${field}`);
  }
  return value;
}

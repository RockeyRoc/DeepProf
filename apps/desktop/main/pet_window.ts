import { BrowserWindow } from "electron";
import { join } from "node:path";

export function createPetWindow(workbenchUrl: string): BrowserWindow {
  const window = new BrowserWindow({
    width: 240,
    height: 280,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: true,
    webPreferences: {
      preload: join(__dirname, "../preload/pet.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  window.setIgnoreMouseEvents(false);
  void window.loadURL(`${workbenchUrl}/?surface=pet`);
  return window;
}

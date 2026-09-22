import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("deepprof", {
  bootstrap: () => ipcRenderer.invoke("app.bootstrap"),
  runtimeStatus: () => ipcRenderer.invoke("runtime.status"),
  provider: {
    configure: (profile: Record<string, unknown>, secret: string) => ipcRenderer.invoke("provider.configure", { profile, secret }),
  },
});

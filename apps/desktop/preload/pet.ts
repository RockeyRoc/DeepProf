import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("deepprofPet", {
  bootstrap: () => ipcRenderer.invoke("pet.bootstrap"),
  interact: (kind: string) => ipcRenderer.invoke("pet.interact", { kind }),
  setIgnoreMouseEvents: (ignore: boolean) => ipcRenderer.invoke("pet.set-ignore-mouse-events", { ignore }),
});

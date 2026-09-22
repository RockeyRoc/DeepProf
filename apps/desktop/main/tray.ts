import { Menu, Tray, nativeImage } from "electron";

export function createTray(onShow: () => void, onToggleThrough: () => void, onQuit: () => void): Tray {
  const tray = new Tray(nativeImage.createEmpty());
  tray.setToolTip("DeepProf");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Toggle pet click-through", click: onToggleThrough },
    { label: "打开工作台", click: onShow },
    { type: "separator" },
    { label: "退出", click: onQuit },
  ]));
  return tray;
}

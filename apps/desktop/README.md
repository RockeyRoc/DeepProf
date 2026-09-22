# DeepProf Desktop（MVP-3）

## 本地启动

```powershell
npm.cmd install
npm.cmd run dev
```

生产构建使用 `npm.cmd run build`。Electron Main 会先在 `127.0.0.1` 动态端口启动
DeepProf Runtime，再在另一个环回动态端口提供工作台页面；Renderer 不拥有 Node 权限，
也不直接访问 Provider、Memory、Tool 或 Secret。

## 当前交付边界

- 首启接入向导通过受控 IPC 将凭据交给 Main；Renderer 只拿到脱敏 Profile。
- 工作台通过 `packages/client_sdk` 发送 `ClientCommand`、消费带 sequence 的 SSE。
- 桌宠是独立透明浮窗，但只通过 `PetDirector` 投影 RuntimeEvent。
- 桌宠图集源文件是 `apps/pet/package/spritesheet.webp`（来源为项目提供的 `pet.png`）；构建前由 `prepare-assets` 同步到 Renderer，再按 v1 的 8×9 动作行切片。

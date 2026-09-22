# MVP-3 验收记录

- 模块：桌面工作台与桌宠挂件
- 版本：DeepProf v0.6.1
- 状态：工程实现完成；桌面端与真实 Provider 链路已执行，外部模型服务仍阻塞最终生成验收
- 范围：`apps/desktop/`、`apps/pet/`、`packages/design_system/`、`packages/client_sdk/`

## 已完成

1. Electron Main / Preload / Renderer 三层桌面骨架。
2. Runtime Supervisor：动态端口、仅监听 `127.0.0.1`、健康检查、退出与重连状态。
3. Backend Broker：仅绑定 loopback，带静态文件路径校验。
4. BrowserWindow 安全边界：`contextIsolation`、`sandbox`、`nodeIntegration: false`、导航限制与 CSP。
5. Main 专用系统凭据存储。Renderer 只提交一次性凭据，不接收凭据回读。
6. Provider Settings：多 Profile、Provider 保存、模型发现、健康探测和状态展示。
7. Desktop 首次接入、Session 创建、Chat 命令、SSE RuntimeEvent 投影、Sources / Trace / Context 占位面板。
8. 独立透明桌宠窗口与 `PetDirector`：按 RuntimeEvent `sequence` 去重，并覆盖 idle / running / waiting / review / failed / jumping / waving。
9. 接入用户提供的 `C:\大创\Prompt\pet.png`，生成 `apps/pet/package/spritesheet.webp`，并按 8×9 atlas 切片。
10. 宠物包构建前校验、窗口拖拽、点击穿透切换、托盘切换和 `pet.interact` IPC。
11. 使用 `pet.png` 的 RGBA 透明背景：同时清除 HTML、Body 和 React 根节点的桌宠窗口背景。
12. Desktop、Onboarding、Provider Settings 和桌宠状态文案支持中文/English 切换，语言偏好保存于本地。
13. Provider Profile 写入 `DEEPPROF_HOME/providers.json`，桌面 Main 进程负责重启时恢复加密凭据到 Runtime。

## 验证记录（2026-09-22）

- `npm.cmd install --ignore-scripts --no-audit --no-fund`：通过。
- `npm.cmd run typecheck`（`apps/desktop`）：通过。
- `npm.cmd run validate`（`apps/pet`）：通过，9 个动作和 spritesheet 校验通过。
- `npm.cmd run build`（`apps/desktop`）：通过，Main、双 Preload、Renderer 与 3.45 MB WebP 资源均生成。
- `tests/test_mvp3_scaffold.py`：6 项轻量结构、安全和交互检查通过。
- `git diff --check`：无空白错误；仅报告 Windows 换行符提示。
- `npm.cmd run dev`：通过；Electron 工作台与桌宠窗口已打开，Runtime `/health` 返回 200，Broker 页面返回 200，均仅使用 `127.0.0.1`。
- `C:\Users\lei\.deepprof`：已创建并写入 GLM OpenAI-compatible Profile；默认角色 `tutor.default` 已指向 `glm` / `glm-4.7-flash`，文件中不保存明文 Key。
- 真实链路：`session.new`、`message.send`、SSE 历史补发均通过；Runtime 事件确认请求实际路由到 `glm` / `glm-4.7-flash`。
- GLM `/models` 探测返回 11 个模型，但当前目录未包含 `glm-4.7-flash`；按用户指定模型保持原配置，没有静默改用其他模型。
- GLM 真实生成探测返回 HTTP 429（provider error code `1302`，账户速率限制），因此本次不能把“真实模型生成成功”标记为通过；Runtime 正确发出 `model.failed` 与 `agent.failed`，没有伪装成成功。

## 尚未通过的最终验收项

- 使用当前 GLM 账户解除速率限制，并确认账户/接口实际提供 `glm-4.7-flash` 后，再重复一次真实模型生成；在这之前不要将本项标记为通过。
- 真实生成成功后，补做一次 Runtime 重启后的桌面状态恢复与 SSE 重连。
- 当前受限环境无法让 pytest 创建默认 `tmp_path` 锁文件，因此没有把完整 pytest 结果标记为通过。此次本地启动使用了可写的 `DEEPPROF_HOME` 临时目录和绝对 Python 路径。

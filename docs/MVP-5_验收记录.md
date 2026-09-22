# MVP-5 验收记录

## 范围

CLI 全流程、Desktop/CLI Session 联调、Provider 接入复测与 Runtime 事件评测；不包含 BKT/IRT。

## 已交付

- `apps/cli/`：交互式 REPL、`login/models/doctor/new/ask/resume/tree/fork/compact`、`--json` 自动化输出。
- Client SDK：Node fetch SSE、按 Session sequence 去重、Session 转录与 Provider client、结构化 Gateway 错误。
- Gateway：Session 转录、默认 Provider 读写与重启恢复、后台 turn 异常的 `agent.failed` 事件。
- 共享环回 Gateway 发现与 attach-or-start；Desktop Main 与 CLI 共用 owner token 生命周期。
- Windows DPAPI 当前用户凭据桥；Desktop 旧 Electron SecretStore 读取与迁移兼容。
- Desktop 会话列表选择、resume、转录展示及版本化活动 Session 偏好。
- `evaluation/`：TTFT、Provider 延迟、端到端相关事件、usage、可选价格、Provider/Surface 审计与 JSON/CSV/Markdown 报告。

## 已执行验证

- CLI TypeScript 编译通过（使用仓库现有 TypeScript 工具链）。
- CLI 离线冒烟通过：`new → ask → resume → tree → compact`，真实 Gateway + Mock Provider，Session 与转录可恢复。
- Desktop typecheck 通过。
- Desktop Electron production build 通过（escalated child-process execution；产物仅写入 ignored `out/`）。
- Python `compileall` 通过；评测聚合固定 fixture smoke 通过。
- 完整 pytest 与 Node `--test` 在当前 Windows 沙箱分别受临时目录 `.lock` 权限和 Node test-runner 子进程 `EPERM` 阻塞；未将环境阻塞记为断言通过。

## 复现

```powershell
$env:DEEPPROF_HOME = "C:\temp\deepprof-mvp5"
cd apps/cli
npm install
npm run build
node dist/apps/cli/src/index.js --json new --title smoke
node dist/apps/cli/src/index.js --json ask <session_id> "hello"
node dist/apps/cli/src/index.js --json resume <session_id>
node dist/apps/cli/src/index.js --json tree
```

评测报告：

```powershell
python -m evaluation.run_mvp5 events.json --pricing evaluation/pricing.example.json --output evaluation/output
```

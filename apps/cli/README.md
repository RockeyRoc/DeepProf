# DeepProf CLI（MVP-5）

CLI 是 Runtime 的键盘优先入口，不直接调用 Provider、Memory 或数据库。它会优先连接 `DEEPPROF_API_URL` 或 `~/.deepprof/gateway.json`，否则启动一个仅监听 `127.0.0.1` 的本地 Gateway。

```powershell
npm install
npm run build
node dist/apps/cli/src/index.js
node dist/apps/cli/src/index.js --json new --title "线性代数"
node dist/apps/cli/src/index.js --json ask <session_id> "什么是梯度下降？"
```

`login` 交互式读取 API Key；自动化场景使用 `--api-key-stdin` 或 `DEEPPROF_SECRET_*`，不支持明文 `--api-key` 参数。Windows 使用当前用户 DPAPI 持久化；其他平台提示使用环境变量。

REPL 命令：`/login`、`/new`、`/ask`、`/resume`、`/tree`、`/fork`、`/compact`、`/models`、`/doctor`、`/quit`。

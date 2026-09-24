# DeepProf CLI（MVP-5）

## 一键安装

需要 Node.js 22+、Python 3.12+ 和可访问的 Python 包索引。无需安装 Git 或下载整个仓库。

```sh
npx --yes --package=https://github.com/RockeyRoc/DeepProf/releases/download/v0.6.2/deepprof-cli-0.6.2.tgz deepprof
```

首次启动自动在 `~/.deepprof/runtime/0.6.2` 准备独立 Python 环境并安装锁定的直接依赖，然后启动仅监听 `127.0.0.1` 的 Gateway。Windows 使用 `%USERPROFILE%\\.deepprof`。配置模型后运行 `deepprof`；运行 `deepprof setup --ocr` 安装可选 OCR 依赖，运行 `deepprof doctor` 检查环境。环境准备仅在 CLI 启动时执行，不运行 npm 安装钩子。

PowerShell 用户也可运行：

```powershell
npx.cmd --yes --package=https://github.com/RockeyRoc/DeepProf/releases/download/v0.6.2/deepprof-cli-0.6.2.tgz deepprof
```

CLI 是 Runtime 的键盘优先入口，不直接调用 Provider、Memory 或数据库。它会优先连接 `DEEPPROF_API_URL` 或 `~/.deepprof/gateway.json`，否则启动一个仅监听 `127.0.0.1` 的本地 Gateway。

```powershell
npm install
npm run build
node dist/apps/cli/src/index.js
node dist/apps/cli/src/index.js --json new --title "线性代数"
node dist/apps/cli/src/index.js --json ask <session_id> "什么是梯度下降？"
```

`login` 交互式读取 API Key；自动化场景使用 `--api-key-stdin` 或 `DEEPPROF_SECRET_*`，不支持明文 `--api-key` 参数。Windows 使用当前用户 DPAPI 持久化；其他平台提示使用环境变量。

REPL 命令：`/login`、`/new [--mode chat|study] [--group A|B|C]`、`/chat <内容>`、`/study <内容>`、`/ask`、`/hint`、`/quiz`、`/answer`、`/learner`、`/ocr <文件>`、`/resume`、`/tree`、`/fork`、`/compact`、`/models`、`/doctor`、`/quit`。普通新会话默认常规聊天；`/new --group A|B|C` 创建教学会话，`/new --mode study` 默认创建 B 组教学会话。教学会话中的明确闲聊自动走聊天回合，教学请求、作答、提示及含糊追问继续教学；`/chat` 和 `/study` 仅指定当前回合路径。实验运行固定教学路径。

`/learner` 只读取 C 组的本地学情估计，至少有 3 条合格作答证据后才显示掌握估计；A/B 组明确显示未启用学情模型。判分回执显示本次是否更新 BKT 或跳过原因。

`/ocr <文件> --first-page N --last-page N --force-ocr` 使用内置 `markitdown` Skill。先安装仓库根目录的 `requirements.txt`；扫描 PDF 和图片还需 `requirements-ocr.txt`。转换在本机离线执行，Markdown 与 JSON 产物写入 `~/.deepprof/course/conversions`（Windows 为 `%USERPROFILE%\\.deepprof\\course\\conversions`）；OCR 文本须人工核对，不会直接进入已审核题库。
首次运行 `login` 时，终端会明确提示 API Key 将显示，并使用可见输入完成首次配置；后续同一 Profile 默认使用隐藏输入。若需要显式选择显示方式，可使用 `login --show-api-key` 或 `login --hidden-api-key`。API Key 不写入 CLI 历史、事件、回放或报告。

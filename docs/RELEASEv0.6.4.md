# DeepProf v0.6.4

本版将本地 CLI／Gateway、网页聊天与非实验学习工作台，以及最新 BKT/RAG 工程实验材料整理为同一正式发行版。

## 安装与升级

需要 Node.js 22+、Python 3.12+；首次准备运行环境需要访问 Python 包索引。

```sh
npm install --global https://github.com/RockeyRoc/DeepProf/releases/download/v0.6.4/deepprof-cli-0.6.4.tgz
deepprof --version
deepprof doctor
deepprof web
```

0.6.4 使用独立运行环境目录，已有用户数据和模型配置继续保留。`deepprof check-update` 检查稳定发行版；扫描件 OCR 通过 `deepprof setup --ocr` 安装。安装和离线验收本身不调用付费模型。

## 网页与 Provider

- 网页可创建聊天或学习会话，管理模型与思考选项，接收流式回复、取消任务、查看资料和回放。网页学习标为“非实验”，不冒充冻结实验批次。
- 文档在本机转换为 Markdown；图片通过本地媒体引用提交给支持视觉的模型。使用远程模型时，发送内容和所选附件会进入该 Provider 的请求。
- 原生联网按 Provider、模型和接口模式判断支持情况；未返回可验证来源的请求不宣称已经取得搜索证据。
- 深度研究收集去重来源，生成可下载 Markdown 报告；取消时保留已经收集的来源。需配置可用的原生搜索或 Tavily 服务。

## API 与兼容行为

Gateway 健康检查及命令／事件契约为 1.9.0，Provider profile 契约为 1.8.0；其他契约保持各自版本。发行版本号不替代 API 契约版本。

| 接口 | 输入与行为 |
|---|---|
| `POST /documents/convert` | 原始请求体为文件字节，`filename` 查询参数用于确定格式；支持 `force_ocr`、`first_page`、`last_page`。返回转换状态、摘要、页数及本地转换产物引用。 |
| `POST /media/images` | 原始图片字节；按魔数校验 PNG／JPEG／GIF／WebP，返回媒体引用。 |
| `GET /media/images/{media_id}` | 读取本地图片引用，校验媒体 ID。 |
| `GET/PUT /settings/web-search` | 查询当前 Provider／模型的原生联网支持和配置，保存或清除搜索凭据；响应不回显凭据。 |
| `POST /settings/web-search/probe` | 显式发起真实联网探测；响应区分 API 请求成功和已返回可验证来源。 |
| `GET/PUT /settings/deep-research` | 查询搜索服务与研究限额，保存或清除 Tavily 配置。 |

会话继续通过现有命令接口与事件流操作。聊天与学习路由、附件、模型思考选项和联网模式由会话／回合设置决定；旧会话和命令的默认行为保留。学习回合仍执行可靠性门控，C 组仅在足够合格作答记录后显示 BKT 估计。研究候选参数不自动进入运行时。

## 实验材料

[公开实验材料](experiments/v0.6.4/README.md)包含最新中文 Markdown／PDF 报告、数值数据、十一张图和绘图源码。

- 全文 v4：120 题、四条件、480 有效格；AI 标注检索 13,710 条、引用 1,595 条、可答性 120 题。
- NoMIRACL 中文：3,770 题、37,599 个固定候选配对，测试 FAR 4.51%、FRR 70.11%、AUC 0.8007。
- BKT：公开开发数据的学生分组嵌套 OOF；题目参数候选改善区分与概率损失，ECE 点估计恶化，仍为探索结果。

人员复核待实际提交；未开展学生学习效果试验。历史失败、停止批次与原始材料在本地归档保留，历史 M1–M3 公开记录继续可查。公开附件不含教材原件、原始请求、私有数据库或密钥。

## 发布校验

发布先通过 Python 回归、M2/M3 离线验收，以及 Windows／Linux／macOS 的 CLI 类型检查、打包、版本与 `doctor` 检查。标签必须匹配包版本及实际检出提交；Release 包含公开证据和 SHA-256 清单。网站在发行附件可用后更新。

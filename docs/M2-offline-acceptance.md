# M2 最新离线工程验收记录

日期：2026-09-24（UTC）  
验收集：`ds-m2-offline-cases-v1`  
工程验收：**通过**  
样本：开发者构造的离线 fixture；未使用真实学生、网络或付费模型。

## 结论与范围

M2 固定数值序列、Attempt 存储与幂等、并发和事务回滚、低样本及不可靠评分、跨会话恢复、冻结参数和 A/B/C 隔离的工程测试通过。它不表示教师已审核题库/策略，不表示 BKT 参数已由真实数据校准，也不表示已证明学生学习效果。

- M2 定向验收：**82 passed，0 failed**；覆盖 BKT 更新顺序、Attempt 资格/重复纪律、SQLite 事务及并发、会话恢复和组间隔离、C 组阈值与非默认冻结参数、题库、OCR 转换、契约和架构守卫。
- Python 全量回归：**309 passed，0 failed，0 skipped**；1 条 Starlette/AnyIO 依赖弃用警告。
- CLI：TypeScript 类型检查通过；CLI 测试 **5 passed，0 failed**。
- OCR fixture 包括文本/扫描 PDF、PNG/JPEG/TIFF、空白扫描页、TXT/Markdown、DOCX、PPTX、页码范围、尺寸限制和缺少本地 OCR 依赖的处理。
- 结构化记录中的失败用例为 `[]`。原始结果、运行配置、执行环境和指纹保存在 [M2-offline-acceptance.json](M2-offline-acceptance.json)。

## 冻结参数和复现信息

- BKT 开发初值：`P(L0)=0.20`、`P(T)=0.10`、`P(G)=0.20`、`P(S)=0.10`，版本 `bkt-four-parameter-dev-v1`；状态为未拟合、待教师审核。
- BKT 配置 SHA-256：`51340c493ab91337ea65b92535dc82237a6e1209ad23a985506e5090f4e8126f`。
- 环境：Python 3.12.14，Windows x64；定向验收用时约 17 秒。MarkItDown 0.1.8、RapidOCR 1.4.4、PDFium 5.13.0。
- 基线提交：`71c5edc24c33fe94ac5ff5fb08715bdeaf655737`；工作树包含既有未提交修改。
- 工作树状态 SHA-256：`10988f14b73e6aef85ad100c264249b60c691e8d39f6bb62124790261dea7cdd`。
- 工作树代码指纹 SHA-256：`b20017ef71509022756b717d2458a9859b98f1d851375998148bf91e185b198c`。

可复现命令：

```powershell
.\.venv\Scripts\python.exe -m evaluation.m2_acceptance --output docs/M2-offline-acceptance.json
.\.venv\Scripts\python.exe -m pytest -o addopts= -q
npm.cmd run typecheck --prefix apps/cli
npm.cmd test --prefix apps/cli
```

## 尚待完成

教师仍需审核题库来源、判分规则、案例预期动作、BKT 参数和策略阈值；BKT 参数仍是开发初值，尚未拟合或校准。尚无教师评分一致性结果，也未测量真实学生学习效果。M1/M2 当前工程闭环通过不替代这些审批与研究条件。本轮没有启动付费模型实验。OCR 内容仍须人工核对后才能进入已审核教材或题库；首版不对 Office 文档内嵌图片执行 OCR。

旧交付状态见[历史 M2 首批记录](M2-first-batch.md)，其中的缺口和实施顺序仅描述当时状态。

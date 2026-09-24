# M3 首批交付：本地离线评测框架

日期：2026-09-24  
框架状态：**首批框架与本地离线试跑通过；M3 研究验收尚未完成。**

本交付使用显式标注的本地 Fake Provider，只验证评测执行、隔离、证据保存、恢复和指标计算链路。开发者构造的案例与 Attempt fixture 不是教师标注，也不代表真实学生或模型表现。

## 本地试跑

最新运行：[summary.json](experiments/m3-offline/m3-offline-20260924-final6/summary.json)；冻结配置和代码指纹：[manifest.json](experiments/m3-offline/m3-offline-20260924-final6/manifest.json)；版本化案例：[cases.json](experiments/m3-offline/m3-offline-20260924-final6/cases.json)。

- 主矩阵：40 案例 × A/B/C = **120/120 格完成**；每格使用独立会话和学习者，C 组按真实 Attempt/BKT 存储管线重建学情；A/B 不写入估计。
- 决策回放：**120/120 格完成**，每个案例和组别在新会话复跑；与主矩阵动作一致 **120/120**。
- RAG 消融：40 案例 × C 组 × 检索开/关 × 证据约束开/关 = **160/160 格完成**。关闭证据约束仅用于此离线实验装配；生产配置仍默认要求证据。
- 三类矩阵合计 400 格，全部完成，400 个会话隔离；失败格 0。
- 本次合成 C 组 Attempt 指标为 120 个可评分预测；AUC、Brier、log loss 和 ECE 只用于检验统计流程，不作为 BKT 有效性结论。
- 决策完整性为 520/520；回放一致性为 120/120。定位引用统计分子/分母为 189/189；策略动作族匹配为 196/280。它们均为 Fake Provider 与构造 fixture 下的框架试跑数据。
- 消融观察到无检索且约束开启时 40 格均阻止生成；无检索且约束关闭时 26 格触发生成。后者验证消融开关可生效，不表示生成内容正确。

逐格 JSON 和事件证据位于 `cells/`、`events/`。两份独立盲评模板位于 [`scoring/rater-01.json`](experiments/m3-offline/m3-offline-20260924-final6/scoring/rater-01.json) 与 [`scoring/rater-02.json`](experiments/m3-offline/m3-offline-20260924-final6/scoring/rater-02.json)，当前均待人工填写。导入会校验 schema、盲评条目和评分范围；评分完成后可计算各字段原始一致率及 Cohen’s κ。未评分字段保持待评。

## 验收边界

- **M2 工程验收状态：**通过，详见[最新 M2 离线验收记录](M2-offline-acceptance.md)。
- **M3 框架状态：**首批离线框架通过；完整性、隔离、恢复和指标边界已验证。
- **真实模型实验状态：**12 案例 × A/B/C 试点已运行 36 格，实际 Provider 请求 29 次、无重试；26 次请求以 `finish_reason=length` 结束，另有 1 次回报 513 completion tokens（配置上限 512）。应用终态完成数不能代替完整模型生成数；该试点不支持模型效果或组间效果结论。详见[真实模型试点记录](experiments/M3-live-pilot.md)、[F08 实际结果图](experiments/figures/F08-m3-live-pilot-results.png)与[F07 本地检索预检图](experiments/figures/F07-m3-live-pilot-preflight.png)。
- **教师审核状态：**待完成。题库、案例预期动作、引用支持和两名评分者判断尚无教师确认。
- **学生学习效果：**未测量。不得把本报告作为教学效果或真实学生增益证据。

运行新实验会创建独立 `run_id`。只有案例、策略、提示、模型、采样、课程、题库、检索、BKT 或源代码指纹均未变化时，才允许用 `--resume` 恢复同一运行；更改配置或代码需新建运行。

```powershell
.\.venv\Scripts\python.exe -m evaluation.m3_acceptance --run-id m3-offline-<new-id>
.\.venv\Scripts\python.exe -m evaluation.m3_acceptance --run-id m3-offline-<new-id> --resume
.\.venv\Scripts\python.exe -m evaluation.m3_acceptance --audit-scores docs/experiments/m3-offline/<run-id>
```

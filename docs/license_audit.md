# 参考项目 License 核查台账

- 核查日期：2026-09-14
- 核查方式：GitHub REST API（`/repos/{owner}/{repo}` 元数据的 `license` 字段 + 仓库根目录文件列表）
- 版本锚点：以核查当日仓库最后推送时间（pushed_at）为准；未发布正式 Release 的项目不虚构版本号
- 借鉴边界：仅参考架构与接口设计思路，不复制任何代码；强传染协议（GPL/AGPL）与无协议项目一律不复制代码

## 核查结果

| # | 项目 | License (SPDX) | 版本锚点（最后推送） | 传染性 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | [openai/codex](https://github.com/openai/codex) | Apache-2.0 | 2026-09-10 | 无（需保留声明与变更说明） | 可借鉴，商用友好 |
| 2 | [1Vewton/dsh-edu](https://github.com/1Vewton/dsh-edu) | MIT | 2026-09-12（commit `6282e73`） | 无 | 可借鉴，商用友好 |
| 3 | [THU-MAIC/OpenMAIC](https://github.com/THU-MAIC/OpenMAIC) | MIT | 2026-09-14（commit `98765db`） | 无 | 可借鉴，商用友好 |
| 4 | [HowieWang1121/Socratic-Education-System](https://github.com/HowieWang1121/Socratic-Education-System) | MIT | 2026-05-03 | 无 | 可借鉴，商用友好 |
| 5 | [Zenglian990/AI_Tutor_Release](https://github.com/Zenglian990/AI_Tutor_Release) | MIT | 2026-09-11 | 无 | 可借鉴，商用友好 |
| 6 | [ZZhouWJ/EduAgent-Studio](https://github.com/ZZhouWJ/EduAgent-Studio) | Apache-2.0 | 2026-07-17 | 无（需保留声明与变更说明） | 可借鉴，商用友好 |
| 7 | [suan-11/mea-pet-public](https://github.com/suan-11/mea-pet-public) | MIT | 2026-09-01 | 无 | 可借鉴，商用友好 |
| 8 | [Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | Apache-2.0 | 2026-09-14 | 无（需保留声明与变更说明） | 可借鉴，商用友好 |
| 9 | [cqzaaa/AgentPet](https://github.com/cqzaaa/AgentPet) | **无 License** | 2026-09-11 | — | **仅概念级参考**，见下方风险说明 |
| 10 | [JulesLiu390/PetGPT](https://github.com/JulesLiu390/PetGPT) | MIT | 2026-09-05 | 无 | 可借鉴，商用友好 |
| 11 | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | MIT | 2026-09-11 | 无 | 可借鉴，商用友好（官方插件化 Harness，dsh-edu 的上游） |

## 风险说明

### AgentPet（无 License）

GitHub API 返回 `license: null`，且仓库根目录文件列表中不存在 LICENSE / COPYING 文件。按版权法默认原则，**未声明许可证的公开代码视为"保留所有权利"（All Rights Reserved）**，不享有复制、修改、分发的授权。

处置措施：
1. 对 AgentPet 仅做**概念层面**参考（如"桌宠 + Live2D + 记忆分层"的产品形态思路），不复制其任何代码、配置与具体接口定义；
2. 若后续确需深入参考，应先向作者申请书面授权，或等待其补充许可证；
3. 申报书与计划书中涉及 AgentPet 的表述统一为"产品形态参考"，不称"已核实兼容"。

### Apache-2.0 项目（openai/codex、EduAgent-Studio、N.E.K.O）

Apache-2.0 允许商用与修改，但若**复制其代码或衍生作品**，须：保留版权声明与许可证、说明修改内容、保留 NOTICE 文件（如有）。本项目当前策略为仅借鉴设计思路，不复制代码，故无额外义务；一旦未来复制任何代码片段，须同步履行上述义务并在本台账登记。

### 未发现强传染协议

11 个参考项目中未发现 GPL / AGPL / SSPL 等强传染协议，不存在传染性风险。

## 维护约定

- 每次新增参考项目时，先在本台账登记核查结果，再写入申报书/计划书；
- 每季度复核一次各项目 License 是否变更（上游可能改协议）；
- 本台账是计划书"5.3 开源借鉴与合规"一节的支撑材料。

# M1–M3 公开证据字段说明

- run_id / 时间：固定运行批次标识；同一 run 内的分母不得与其他历史批次拼接。
- sample_type：全部标为构造开发 fixture；human_subjects=false 表示无真人参与者。
- planned / observed / completed：计划格、存在记录的格和正常完成格，三者含义不同。
- M2 passed/total：固定测试项数；82 项定向验收包含于 309 项 Python 全量回归。
- provider_requests / finish_reason：真实模型请求次数和终止类型；length 记为截断失败。
- application terminal：软件终态；不等价于评测完整生成或答案正确。
- locatable references：引用定位字段是否完整；不表示语义支持度。
- AUC / Brier / log loss / ECE：仅针对 120 条构造预测；较差指标如实保留，不代表学生预测效能。
- code_fingerprint：优先采用运行清单中保存的 commit 与工作树哈希；M1 导出记录缺少可核验的哈希值。
- F07 为历史预检，F08 为真实 Provider 试跑，F09 为 Fake Provider 离线试跑。
- v0.6.2 发布核验是独立的软件回归批次，不替代或覆盖历史 M1–M3 指标。
- v0.6.2 M2/M3 发布复核 JSON 由脱敏导出脚本生成；仅包含聚合值、测试文件名和源记录哈希。
- 不在公开包内提供教材、答案、模型输出正文、提示正文、检索片段、密钥或绝对本机路径。

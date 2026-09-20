"""教育组实验包（DESIGNv0.4 §16.3 / §12）。

与单测（FakeRuntime，provider=fake）互补：本包的脚本走**真实链路**——
真实 RuntimeService（真实 Provider / Skill / 绑定表 / SQLite）+ 教学策略图，
用于教师评审与验收记录里的"真实实验"部分。

安全约定：密钥只从 .env 读取，任何输出（日志 / CSV / 报告）都不得出现完整密钥。
"""

"""DeeepProf 全局配置。

从 .env 文件加载，密钥不硬编码。
切换 LLM 只需改 .env 中的 LLM_PROVIDER，业务代码无需改动。
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。

    通过环境变量或 .env 覆盖默认值。
    所有密钥字段默认空串，缺 key 时由 adapter 层抛清晰错误。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里有未声明字段时忽略，避免报错
    )

    # ========== LLM 适配层 ==========
    llm_provider: str = "deepseek"  # deepseek | openai | qwen

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-luna"

    # 本地/云端 Qwen（通过 OpenAI 兼容接口）
    qwen_api_key: str = ""
    qwen_base_url: str = "https://api.qnaigc.com/v1"
    qwen_model: str = "qwen/qwen3.8-flash-next"

    # 对话参数
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    # 单次模型调用的连接/读空闲超时（秒）。SDK 默认 600s 曾导致流式挂起
    # 10 分钟才失败；调小以便快速失败并进入降级文案
    llm_timeout_seconds: float = 120.0

    # ========== 记忆与存储（DESIGNv0.4 D-3：抽象接口 + 本地 SQLite） ==========
    # 留空 = 用户数据目录默认位置（见 config/paths.py）：
    #   sqlite_path   → ~/.deepprof/sessions/deepprof.db
    #   vector_db_path → ~/.deepprof/memory/vector_store
    # 开发者可在 .env 里写相对路径（相对项目根）或绝对路径覆盖。
    sqlite_path: str = ""
    vector_db_path: str = ""
    sqlite_busy_timeout_ms: int = 5000  # WAL 下的写锁等待，避免并发写直接失败

    # ========== Runtime ==========
    runtime_source: str = "deepprof.runtime"  # 事件默认 source，便于轨迹溯源
    agent_max_turns: int = 8  # 单次 run 的模型—工具循环上限，防止无限循环
    # 单次工具执行的超时上限（秒）。工具可能有网络/磁盘 IO，挂起会把 Agent 循环
    # 无限拖住（与 llm_timeout_seconds 同一类防护）；工具可用 timeout_seconds 覆盖
    tool_timeout_seconds: float = Field(default=30.0, gt=0)
    session_compact_keep: int = 12  # compact 时保留最近 N 条消息
    trace_id_header: str = "x-trace-id"  # API 侧透传 trace_id 的请求头

    # ========== Sandbox（DESIGNv0.4 D-5：目录白名单 + 审批） ==========
    # 逗号分隔：相对路径相对项目根，~/ 开头相对用户主目录（会话库/记忆在 ~/.deepprof，
    # 工具要能读写它们，故默认放行）
    sandbox_allowed_roots: str = "data,media,docs,~/.deepprof"
    sandbox_allow_network: bool = False
    sandbox_allow_process: bool = False

    # ========== Plugin（DESIGNv0.4 D-4：仅受信 Python 插件） ==========
    # 留空 = ~/.deepprof/plugins（用户装的插件跟用户数据走，不污染程序目录）
    plugin_dir: str = ""  # 第三方插件扫描目录
    plugin_trusted_only: bool = True  # 只加载受信、审核后的 Python 插件
    # 运维维护的插件信任清单（外部裁定）：清单外的插件一律拒绝安装。
    # 放在插件包之外是关键——plugin.json 里的 trusted 由插件自己写，不能作为依据。
    plugin_trust_store: str = "plugins/trusted.json"

    # ========== 宠物侧 ==========
    affinity_init: int = Field(default=30, ge=0, le=100)  # 好感度初值 [0,100]


# 单例：业务层直接 `from config import settings`
settings = Settings()

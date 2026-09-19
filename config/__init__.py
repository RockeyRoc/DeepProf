"""config 包：全局配置加载。

密钥与可变参数全部从 .env 读取，不硬编码。
使用：from config import settings
"""
from .paths import PROJECT_ROOT, ensure_parent, project_path
from .settings import settings

__all__ = ["settings", "PROJECT_ROOT", "project_path", "ensure_parent"]
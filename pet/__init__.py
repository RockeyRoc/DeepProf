"""桌宠表现层契约（DESIGNv0.4 §16.4，归属张钧翔；本轮只做骨架）。

包含两部分：
- ``emotion``：教学动作 → 情绪/表情标签的**可解释映射**（前端表现层用）；
- ``affinity``：好感度状态骨架（初值取 ``settings.affinity_init``，尚未接真实事件源）。

本轮不做的事（§16.4 下一轮）：
- 不做 Live2D / PNG 渲染，不做 ASR/TTS，不写 Electron 代码；
- 不把好感度写入学情模型：好感度只是表现层状态，
  不得反向影响诊断或给学生贴标签（§13.1）。

依赖方向（§9.1）：pet 只定义可被前端消费的数据形状，
不导入 runtime / graph，也不访问数据库。
"""
from .affinity import AffinityState
from .emotion import EmotionDirective, EmotionLabel, TeachingAction, emotion_for, mapping_table

__all__ = [
    "AffinityState",
    "EmotionDirective",
    "EmotionLabel",
    "TeachingAction",
    "emotion_for",
    "mapping_table",
]
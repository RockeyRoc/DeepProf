"""教学策略常量与可评审模板（DESIGNv0.4 §6.2 / §6.3 / §7.3 / §18.1）。

把这些"教师在什么条件下怎么做"的数字与话术集中在一处，好处是：
- 唐欢容（指导教师）可以只审这一个文件就完成教学策略评审（§16.7）；
- 阈值改动不需要动节点代码，分支测试也不会因为文案漂移而失效；
- 每个常量都写明设计依据章节，便于申报材料追溯。

本模块只放纯数据与纯函数，不 import LangGraph、不 import Runtime 实现，
因此可以被任何节点、路由与测试直接使用。
"""
from __future__ import annotations

# ======================================================================
# 一、循环与退出（§7.3：图循环超限触发 Reflect 或结束，避免无限提问）
# ======================================================================

#: 单次教学策略会话允许的最大轮次（§16.3"加入最大轮次、退出与 Reflect 回退"）
MAX_TURNS_DEFAULT = 6

#: 编译图时的 LangGraph recursion_limit 兜底：正常路径远小于该值
RECURSION_LIMIT_MARGIN = 10


def recursion_limit(max_turns: int) -> int:
    """给 LangGraph 的执行步数上限（最后一道防线，正常不会触发）。"""
    return 4 * max(int(max_turns or MAX_TURNS_DEFAULT), 1) + RECURSION_LIMIT_MARGIN


#: Assess 决策出这些值时，图直接结束（不再进入教学动作节点）
END_ACTIONS = ("end", "stop", "finished")


# ======================================================================
# 二、六类教学动作（§6.2 教学决策节点）
# ======================================================================

ACTION_TEACH = "teach"  # 概念缺失且适合直接解释
ACTION_ASK = "ask"  # 学生具备推理基础 → 苏格拉底追问
ACTION_HINT = "hint"  # 尝试受阻但不宜直接给答案
ACTION_CORRECT = "correct"  # 出现稳定错误或概念混淆
ACTION_TEST = "test"  # 需要验证理解或间隔复习
ACTION_REFLECT = "reflect"  # 多轮无进展、异常或策略失效
ACTION_END = "end"  # 本轮结束（学生停止或已完成）

#: Assess 节点向 Runtime 索取"本轮教材证据"时用的动作名。
#: 它**不是**六类教学动作之一，只是"取证据"这个信息请求的名字——因此刻意
#: 不加入 ACTION_NODES：route_from_assess 会把 ACTION_NODES 里的动作映射成节点，
#: 若把 assess 放进去，next_action=assess 就会映射回 assess 自身，形成自环。
ACTION_ASSESS = "assess"

#: 读学情记忆（Assess 判断依据之二）与把本轮结果落成学情增量（UpdateProfile）。
#: 与 ACTION_ASSESS 同样属于"信息请求/落盘请求"，不是教学动作，同样不进 ACTION_NODES。
ACTION_RECALL = "recall"
ACTION_UPDATE_PROFILE = "update_profile"

#: 问学情模型要结构化估计（当前返回 not_implemented，图侧据此退化为规则化观察，§18.1）。
ACTION_DIAGNOSE = "diagnose"

#: 动作 → 图节点名（reflect 有自己的节点，end 落到 END）
ACTION_NODES = {
    ACTION_TEACH: "teach",
    ACTION_ASK: "ask",
    ACTION_HINT: "hint",
    ACTION_CORRECT: "correct",
    ACTION_TEST: "test",
    ACTION_REFLECT: "reflect",
}

#: 动作 → 情感标签（§16.3 交前端；桌宠侧据此驱动表情，张钧翔消费）
EMOTION_BY_ACTION = {
    ACTION_TEACH: "explaining",
    ACTION_ASK: "curious",
    ACTION_HINT: "encouraging",
    ACTION_CORRECT: "patient",
    ACTION_TEST: "attentive",
    ACTION_REFLECT: "supportive",
    ACTION_END: "warm",
}


# ======================================================================
# 三、Hint 升级规则（§6.2 Hint：从轻到重给提示）
# ======================================================================

#: 提示级别上限（1 最轻 → 3 最重；3 级仍不给最终答案）
HINT_MAX_LEVEL = 3

#: 连续答错达到该次数即升级一级提示（第 3 级封顶）
HINT_ESCALATE_WRONG_STREAK = 1

#: 各级提示模板：级别越高越具体，但都保留学生自己的推理空间。
#: 递增顺序即"脚手架递增"：1 只问定义 → 2 换表征对照 → 3 给第一步。
#: 不变量：任何一级都不给出最终答案；第 3 级起开始给脚手架，因此显式声明这一点。
HINT_LEVEL_TEMPLATES: dict[int, str] = {
    1: ("先回到定义：就「{concept}」，你能说清定义里每个条件分别起什么作用吗？"),
    2: (
        "换一个角度试试：把「{concept}」和相邻概念对照一下——"
        "它在什么条件下成立？条件不满足会怎样？（我只指出方向，不给结论）"
    ),
    3: (
        "给你第一步：先把已知条件与目标写成式子，再判断该用哪条定理；"
        "后续推导仍由你完成，我不直接给出最终答案。"
    ),
}


def next_hint_level(current_level: int, wrong_streak: int) -> int:
    """按"连续答错则升级提示"计算本轮提示级别（§6.2 Hint）。

    - 尚未给过提示（0）→ 从最轻一级开始；
    - 连续答错 ≥ HINT_ESCALATE_WRONG_STREAK → 升一级，最多到 HINT_MAX_LEVEL；
    - 未答错（例如学生主动求助）→ 维持当前级别，不无故加码。
    """
    level = max(int(current_level or 0), 0)
    if level <= 0:
        return 1
    if int(wrong_streak or 0) >= HINT_ESCALATE_WRONG_STREAK:
        return min(HINT_MAX_LEVEL, level + 1)
    return min(HINT_MAX_LEVEL, level)


# ======================================================================
# 四、Correct / Test 触发阈值（§6.2）
# ======================================================================

#: 连续答错达到该次数 → 判定为"稳定错误"，转入 Correct 直接纠错
CORRECT_WRONG_STREAK = 3

#: 累积误解条目达到该数量 → 判定为"概念混淆"，转入 Correct
MISCONCEPTION_LIMIT = 2

#: 状态中保留的误解条数上限（避免状态无界增长）
MISCONCEPTION_KEEP = 5

#: 尝试次数达到该值且本轮无未处理错误 → 进入 Test 验证理解
QUIZ_AFTER_ATTEMPTS = 3

#: 同一轮内"答错后回退重讲"的最大次数（配合 hint_level / turn_count 限界）
TEST_RETRY_MAX_ROUNDS = 1

#: 主动求讲解的意图关键词（§16.3 教学样例"主动求讲解"）
EXPLAIN_INTENT_KEYWORDS = (
    "讲讲",
    "讲一下",
    "讲解",
    "解释一下",
    "解释下",
    "介绍一下",
    "什么是",
    "是什么",
    "怎么理解",
    "说明一下",
    "不会",
    "不懂",
    "没听懂",
    "不明白",
)


def requests_explanation(text: str) -> bool:
    """学生是否明确要求讲解（用于 Teach 分支触发条件）。"""
    content = str(text or "")
    return any(keyword in content for keyword in EXPLAIN_INTENT_KEYWORDS)


# ======================================================================
# 五、证据与引用（§7.3：RAG 证据不足禁止伪造引用；§12 不编造引用）
# ======================================================================

#: RAG 检索条数
EVIDENCE_TOP_K = 4

#: 送入模型前每条证据原文的截断长度（避免超长与不必要的隐私复制）
EVIDENCE_MAX_TEXT_CHARS = 600

#: 证据不足时 Teach 的固定表述：不给任何来源，并显式说明证据不足
TEACH_INSUFFICIENT_EVIDENCE_TEXT = (
    "（证据不足）教材检索没有返回可定位的原文片段，因此本轮讲解不给出任何来源与引用。"
    "下面只是通用的思考路径，不作为教材结论：先回到定义与前置概念，"
    "确认已知条件与目标，再检查可用定理的适用条件。"
    "如果你希望我给出有出处的讲解，请确认教材是否已入库，或换一个更小的知识点。"
)

#: 证据不足时 Correct 的固定表述（同样不给来源）
CORRECT_INSUFFICIENT_EVIDENCE_TEXT = (
    "（证据不足）教材检索没有返回可定位的原文片段，因此本轮纠错不给出任何引用来源。"
    "就目前信息看，你的说法与「{concept}」的常用定义存在冲突：{conflicts}。"
    "请回到定义核对条件的适用范围，再给我一个「正确/错误示例」的对比例，"
    "我们据此判断是否需要修正。"
)

#: 模型调用失败时的表述（§7.3 失败必须结构化、可解释）
GENERATION_FAILED_TEXT = (
    "（生成失败）本轮模型调用没有返回内容，我先不给结论。"
    "我们可以换一个更小的知识点继续，或稍后重试。"
)

#: 证据不足时 Ask 的说明（追问本身不需要引用，但必须说明没有检索到原文）
ASK_NO_EVIDENCE_NOTE = "（说明：本轮未检索到可定位的教材原文，以下追问不含引用。）"

#: Ask 兜底问题模板（苏格拉底 Skill 不可用时使用，避免直接泄露答案）
ASK_FALLBACK_QUESTION = (
    "我们一步步推理：针对「{concept}」，你认为第一步应该先确定什么？为什么？"
)

#: Reflect 使用的策略调整建议（§6.2 Reflect：回退、换策略或求助教师）
REFLECT_TEXT = (
    "这一轮我们反复尝试但进展有限，继续原地追问效率不高。建议换一种方式："
    "① 回退到「{concept}」的前置概念重新确认；"
    "② 换一种表征方式（画图、举反例或代入数值）；"
    "③ 请老师介入复核这个知识点的要求。"
    "你选一条，我们下一轮从这里继续。"
)

#: 上面三条建议的机器可读版本（决策事件用，供实验统计"选了哪种换策略方向"）。
#: 与 REFLECT_TEXT 的 ①②③ 是同一份策略，改一处必须改另一处。
REFLECT_STRATEGY_OPTIONS = ("回退前置概念", "换表征方式", "请教师介入")

#: 学生主动停止时的收束语（§16.3：学生停止时退出）
STOPPED_TEXT = (
    "好，本轮先到这里。我已经把本轮的学习情况写入可撤回的学情记忆，"
    "你随时可以让我更正或删除它。下次我们接着这个知识点继续。"
)

#: Test 节点必须声明的限制（诚实原则：题库与自动判分尚未接入）
QUIZ_NOT_CONNECTED_NOTE = (
    "（说明：课程题库与自动判分尚未接入——题目由模型即时生成，"
    "判分结论不写入学情；待课程题库与 Attempt 链路补齐后再启用正式测验。）"
)

#: Quiz Skill 不可用时的兜底自检题（仍然不给答案）
QUIZ_FALLBACK_ITEM = (
    "请你用自己的话复述「{concept}」的定义，并说明它的适用条件与一个反例。"
)

#: Test 回复的"框架"键：节点按 mode 与判分三态算出键，bindings 声明每个键
#: 对应哪段文案。键放在 policies 是因为两边都要引用它——写歪一个字符不会报错，
#: 只会让回复少一段框架，所以必须共用同一份常量。
REPLY_FRAME_QUIZ = "quiz"  # 出题（本轮没有作答可评价）
REPLY_FRAME_CORRECT = "correct"  # 判为正确
REPLY_FRAME_INCORRECT = "incorrect"  # 判为错误
REPLY_FRAME_UNKNOWN = "unknown"  # 判分缺失（自动判分未接入）

#: 出题回复的开头
QUIZ_ITEM_PREFIX = "【自检】"

#: 评价回复的开头：三态判分必须分开措辞——§13.1 不把"判不了"当成"答错"，
#: 所以 REPLY_FRAME_UNKNOWN 用"先对齐"而不是"还有问题"。
QUIZ_FRAME_CORRECT = "这一步是对的。"
QUIZ_FRAME_INCORRECT = "这一步还有问题："
QUIZ_FRAME_UNKNOWN = "我们先对齐一下："

#: Quiz Skill 没给出评价内容时的兜底正文
QUIZ_EVALUATION_FALLBACK = "请把你的推理过程再写一步，我们逐句检查。"

#: 判分信息缺失时的说明（不得把"判分不了"当成"答错"，§13.1 不贴永久标签）
QUIZ_NO_JUDGEMENT_NOTE = (
    "（判分未接入）我无法确认这一步的对错，因此本轮不更新你的错误计数，"
    "也不会把结论写成学情标签。请补充你的推理过程，我们继续讨论。"
)


# ======================================================================
# 六、记忆写入（§5.5 任何长期记忆都必须带来源、置信度、过期策略与可撤回标记）
# ======================================================================

MEMORY_READ_LIMIT = 10

#: 规则化观察的置信度基线（BKT 未接入前的最低可解释基线，§18.1）
MEMORY_CONFIDENCE_BASE = 0.30
MEMORY_CONFIDENCE_WITH_EVIDENCE = 0.20
MEMORY_CONFIDENCE_WITH_ATTEMPT = 0.10
MEMORY_CONFIDENCE_CAP = 0.60

#: 记录学情估计来源的模型版本；接入 BKT/IRT 后由数据组替换（§18.1）
RULE_MODEL_VERSION = "rule_based_v0_no_bkt"

#: 误解条目的记忆键前缀与标签字段（写入方 UpdateProfile 与读取方 Assess 共用）。
#: 这两处原本各写一遍字符串，等于一个契约两个主人：改一处忘另一处不会有任何报错，
#: 只会让"历史误解"静默读不到。集中到这里后，约定本身成为可评审的策略常量。
MISCONCEPTION_KEY_PREFIX = "misconception:"
MISCONCEPTION_LABEL_FIELD = "misconception"


def misconception_key(concept: str, label: str) -> str:
    """误解条目的记忆键（形如 ``misconception:<concept>:<label>``）。"""
    return f"{MISCONCEPTION_KEY_PREFIX}{concept or 'general'}:{label}"


def is_misconception_record(record: dict) -> bool:
    """该记忆记录是否为误解条目（按键前缀判定，不猜内容）。"""
    return str(record.get("key") or "").startswith(MISCONCEPTION_KEY_PREFIX)


def misconception_label(record: dict) -> str:
    """从误解条目里取出标签；取不到返回空串，由调用方决定跳过。"""
    metadata = record.get("metadata") or {}
    return str(metadata.get(MISCONCEPTION_LABEL_FIELD) or "").strip()


#: 规则化观察不得被解读成掌握结论的免责表述（§13.1 不给学生贴永久标签）
MEMORY_DISCLAIMER = "该结论由规则生成，未接入 BKT/IRT 学情模型，不代表已掌握或未掌握。"


def memory_confidence(evidence_sufficient: bool, attempt_count: int) -> float:
    """规则化置信度：证据越充分、作答痕迹越多，观察越可信（§18.1）。"""
    value = MEMORY_CONFIDENCE_BASE
    if evidence_sufficient:
        value += MEMORY_CONFIDENCE_WITH_EVIDENCE
    if int(attempt_count or 0) > 0:
        value += MEMORY_CONFIDENCE_WITH_ATTEMPT
    return round(min(value, MEMORY_CONFIDENCE_CAP), 2)


# ======================================================================
# 七、RuntimePort 调用与事件约定
# ======================================================================

#: 图内所有调用与事件的来源标识（便于事件回放时按 source 过滤，§5.4）
GRAPH_SOURCE = "deepprof.graph.education"

#: 事件类型使用 EventType 的字符串值（runtime/core/events.py 冻结契约）
EVENT_NODE_ENTERED = "pedagogy.node.entered"
EVENT_DECISION = "pedagogy.decision"
EVENT_NODE_EXITED = "pedagogy.node.exited"

#: 模型调用的默认温度：讲解/纠错要稳，追问要有点变化
GENERATE_TEMPERATURE = 0.3

#: 教学 prompt 的系统提示（与 runtime/service.py 的 SYSTEM_PROMPT 同源口径）
TEACH_SYSTEM_PROMPT = (
    "你是 DeepProf，一名面向高校学生的伴学老师。用中文回答，语气亲切但严谨。"
    "只能依据给定的教材片段作答，不得引入片段之外的文献或来源；"
    "没有片段时明确说明证据不足，不编造引用。"
)
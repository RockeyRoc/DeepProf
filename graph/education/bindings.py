"""教学动作 → 能力绑定表（DESIGNv0.4 §4.4 / §6.2 / §6.3）。

这是 WHAT 与 HOW 之间**唯一的接缝**，而且是纯数据，不是代码：

- Runtime 不认识 hint / teach / ask 这些词，只按表把决策翻成通用能力调用；
- 教学策略组改这张表就能改"用哪段模板、调哪个 Skill、喂什么提示词"，
  Runtime 与节点都不用动；
- 绑定表放在 graph 侧而不是 runtime 侧，是因为 runtime 不得反向依赖教学词汇
  （tests/test_architecture_boundaries.py 有词汇守卫）。它由组合根
  （api/app.py）注入 RuntimeService。

绑定字段（解析逻辑见 runtime/capabilities.py）：

    capability         要执行的通用原语名
                       （render_template / invoke_skill / retrieve_evidence / generate_grounded）
    params             原语参数；字符串中的 ``${字段}`` 从决策 dict 取值

    --- 以下是"绑定模板块"，四种字段共用同一套语义 ---
    prefix / suffix    围绕正文的段落（开头框架、结尾说明）；未声明即不加
    fallback           主能力失败时的确定性兜底（宁可话术朴素，不让模型自由发挥）
    insufficient_text  证据不足时的确定性表述（不调用模型）

    模板块有两种形态，二者可混用同一种写法：
        {"template": "...", "values": {...}}                      固定模板
        {"templates": {...}, "select": "<键>", "values": {...}}   按取值选模板
    ``select`` 指名的键由 ``values`` 提供（通常经 ``${...}`` 从决策取值）。
    声明了 select 却选不到模板时**必须显式失败**：静默少一段会让学生看到
    半截回复，而绑定作者以为自己配好了。

    evidence           require_evidence=True 时先执行的能力；取不到证据就不执行主能力

原语里两个"取哪一段"的参数：

    invoke_skill.content_field   取 Skill 结果的哪一段作正文，支持 ``a.b`` 点路径；
                                 也可写成模板块，按 mode 决定读哪里（见 ACTION_TEST）
    invoke_skill.passthrough     Skill 结果里要原样带回决策层的字段**白名单**
                                 （如 item_bank_connected），白名单外一律不带回

字段归属（同一件事只留一个主人）：

    action / concept / level / evidence_sufficient   ── 决策（Graph 侧）
    用哪段模板 / 调哪个 Skill / 提示词怎么写         ── 本表（数据）
    证据是否可定位、模型是否被调用                   ── Runtime 能力层

三条有意为之的取舍：

1. **Hint 绑到 render_template，不经过模型**。提示强度是教学实验的自变量
   （§3.4 研究问题 1），如果让模型改写提示语，level 之间的差异会被措辞噪声污染，
   `reveal_answer=False` 也就退化成一句 prompt_only 的声明。
2. **Ask 的兜底与"不含引用"说明写在这里**，而不是写进 Socratic Skill：
   它们都是教学策略（宁可问题朴素，也不让模型自由发挥），属于可评审的策略数据。
3. **Test 的正文拼装也在这里**（按 mode 取 Skill 结果的不同位置、按 reply_frame
   选框架文案）。代价是这张表比别处长；收益是"判分三态怎么措辞"这类策略
   全在一处可评审，而不是散在节点代码里。

六类教学动作（teach / ask / hint / correct / test / reflect）都已迁移；此外
`assess` 与 `end` 不是教学动作，而是"Assess 节点要取的信息"与"收束语"：
前者让 Assess 不再自己调 RAG，后者让收束语不再由节点自己拼。
"""
from __future__ import annotations

from typing import Any

from .policies import (
    ACTION_ASK,
    ACTION_ASSESS,
    ACTION_CORRECT,
    ACTION_DIAGNOSE,
    ACTION_END,
    ACTION_HINT,
    ACTION_RECALL,
    ACTION_REFLECT,
    ACTION_TEACH,
    ACTION_TEST,
    ACTION_UPDATE_PROFILE,
    ASK_FALLBACK_QUESTION,
    ASK_NO_EVIDENCE_NOTE,
    CORRECT_INSUFFICIENT_EVIDENCE_TEXT,
    EVIDENCE_MAX_TEXT_CHARS,
    EVIDENCE_TOP_K,
    GENERATE_TEMPERATURE,
    GENERATION_FAILED_TEXT,
    HINT_LEVEL_TEMPLATES,
    MEMORY_READ_LIMIT,
    QUIZ_EVALUATION_FALLBACK,
    QUIZ_FALLBACK_ITEM,
    QUIZ_FRAME_CORRECT,
    QUIZ_FRAME_INCORRECT,
    QUIZ_FRAME_UNKNOWN,
    QUIZ_ITEM_PREFIX,
    QUIZ_NO_JUDGEMENT_NOTE,
    QUIZ_NOT_CONNECTED_NOTE,
    REFLECT_TEXT,
    REPLY_FRAME_CORRECT,
    REPLY_FRAME_INCORRECT,
    REPLY_FRAME_QUIZ,
    REPLY_FRAME_UNKNOWN,
    STOPPED_TEXT,
    TEACH_INSUFFICIENT_EVIDENCE_TEXT,
    TEACH_SYSTEM_PROMPT,
)

#: Teach 的提示词模板（绑定侧是唯一来源；节点已不再自带一份）。
#: 起讲点由 `{prior_gap_note}` 决定——它来自 policies.teach_prior_note：
#: 先验不足时要求先补前置概念，先验正常时精简基础步骤（§16.3 先验不足样例）。
#: `{memory_note}` 是 Assess 召回并压缩后的学情记忆摘要（policies.memory_note），
#: 只作个性化参考，明确声明它不是教材依据（§7.3 引用纪律）。
TEACH_PROMPT_TEMPLATE = (
    "请针对知识点「{concept}」做分层讲解。\n"
    "学习目标：{learning_goal}\n"
    "{prior_gap_note}\n"
    "{memory_note}\n"
    "学生的问题：{user_input}\n\n"
    "教材片段（唯一允许的依据）：\n"
    "{evidence_block}\n\n"
    "要求：\n"
    "1) 只用中文，先给 2-3 句结论性概述，再分 2-3 步展开；\n"
    "2) 每个结论都必须能对应到上面的片段，不得引入片段之外的文献或来源；\n"
    "3) 最后给出一个自检问题，让学生自己检验是否理解。"
)

#: Correct 的提示词模板（三部分：冲突点 / 原因 / 对比例）。
CORRECT_PROMPT_TEMPLATE = (
    "学生在知识点「{concept}」上出现了稳定错误。\n"
    "学生的说法/疑似误解：{conflicts}\n"
    "学生的原话：{user_input}\n"
    "{memory_note}\n\n"
    "教材片段（唯一允许的依据）：\n"
    "{evidence_block}\n\n"
    "要求：用中文输出三部分，不要引入片段之外的文献或来源：\n"
    "1) 冲突点：明确指出学生说法与片段中定义/条件的矛盾之处；\n"
    "2) 原因：解释为什么这样理解会出错（通常是条件或适用范围被忽略）；\n"
    "3) 对比例：给出一个正确示例与一个错误示例的对照，让学生自己判断差别。"
)

#: 证据获取：Assess 探路、Teach / Correct 正式取用，都用同一段检索规格
#: （同一门课、同一份教材）。query / concept 由节点填进决策，这里不替它猜。
_EVIDENCE_BINDING = {
    "capability": "retrieve_evidence",
    # 同一轮内同一规格只真正检索一次：Assess 的探路结果直接给 Teach / Correct 复用。
    # 只读且同轮内结果稳定才可声明 memo——写学情、调模型这类有副作用的能力绝不能加。
    "memo": True,
    "params": {
        "skill": "rag",
        "input": {
            "query": "${params.query}",
            "concept": "${concept}",
            "top_k": EVIDENCE_TOP_K,
        },
    },
}


ACTION_BINDINGS: dict[str, dict[str, Any]] = {
    # --------------------------------------------------------------- Assess
    # Assess 不产出正文，只判断；它需要教材证据来给状态定 evidence_sufficient，
    # 因此这里的"动作"是一次信息请求：把证据取回来交给节点判断。
    # 不设 require_evidence 门：Assess 要的正是"有没有证据"这个信号本身，
    # 设了门反而会先取一次、再取一次。
    ACTION_ASSESS: _EVIDENCE_BINDING,
    # --------------------------------------------------------------- Recall
    # 读学情记忆：身份（learner_id）由能力层从调用上下文取，这里只声明检索口径——
    # "查哪一类记忆、查多少条、key 用什么"才是策略。
    ACTION_RECALL: {
        "capability": "read_memory",
        "params": {
            "query": {
                "memory_type": "long_term",
                "key": "${concept}",
                "limit": MEMORY_READ_LIMIT,
            },
        },
    },
    # -------------------------------------------------------------- Diagnose
    # 问学情模型要结构化估计：**不声明 content_field**，因此这是"只问状态"的调用——
    # 模型未接入时 Skill 回 not_implemented 也算调用成功（结论在 skill_status 里），
    # 图侧据此退化为规则化观察（§18.1），而不是把"没接入"当成失败。
    ACTION_DIAGNOSE: {
        "capability": "invoke_skill",
        "params": {
            "skill": "diagnosis",
            "input": {
                "learner_id": "${params.learner_id}",
                "concept": "${concept}",
                "attempt_count": "${params.attempt_count}",
                "wrong_streak": "${params.wrong_streak}",
                "hint_level": "${params.hint_level}",
                "misconceptions": "${params.misconceptions}",
                "last_answer_correct": "${params.last_answer_correct}",
                "recent_actions": "${params.recent_actions}",
                "evidence_count": "${params.evidence_count}",
            },
        },
    },
    # -------------------------------------------------------- UpdateProfile
    # 落学情增量：记录本身由策略层构造（来源/置信度/过期策略/可撤回都在记录里，
    # §5.5），这里只声明"把它写进去"。空列表 = 本轮没有可写的增量。
    ACTION_UPDATE_PROFILE: {
        "capability": "write_memory",
        "params": {"records": "${params.records}"},
    },
    # ------------------------------------------------------------------ End
    # 学生主动停止时的收束语：纯模板，不经模型（收束语不该被模型改写）。
    ACTION_END: {
        "capability": "render_template",
        "params": {"template": STOPPED_TEXT, "values": {}},
    },
    # -------------------------------------------------------------- Reflect
    # 两条分支都是确定性模板：换策略建议 / 学生已停止的收束语。
    # 说明：stopped=True 这一支目前**走不到**——route_from_assess 在学生停止时
    # 直接返回 END，Assess 也已经把收束语交给了 ACTION_END。保留它是防御性的
    # （节点被外部直接调用时不至于给出空回复），不是当前链路上的分支。
    ACTION_REFLECT: {
        "capability": "render_template",
        "params": {
            "templates": {"false": REFLECT_TEXT, "true": STOPPED_TEXT},
            "select": "stopped",
            "values": {"concept": "${concept}", "stopped": "${params.stopped}"},
        },
    },
    # ---------------------------------------------------------------- Hint
    # 确定性渲染：level → 模板，绝不调用模型。
    # reveal_answer=False 由结构保证（模板里没有最终答案），不靠提示词约束。
    ACTION_HINT: {
        "capability": "render_template",
        "params": {
            "templates": {str(level): text for level, text in HINT_LEVEL_TEMPLATES.items()},
            "select": "level",
            "values": {"concept": "${concept}", "level": "${level}"},
        },
    },
    # ---------------------------------------------------------------- Ask
    # 派人：Socratic Skill 生成递进追问；不可用时退回策略模板，
    # 并在本轮证据不足时显式说明"不含引用"（避免学生误以为问题来自教材）。
    ACTION_ASK: {
        "capability": "invoke_skill",
        "params": {
            "skill": "socratic",
            "content_field": "question",
            "input": {
                "concept": "${concept}",
                "learning_goal": "${params.learning_goal}",
                "learner_input": "${params.user_input}",
                "hint_level": "${level}",
                "prior_attempts": "${params.attempt_count}",
                "memory_note": "${params.memory_note}",
                "avoid_answer": True,
            },
        },
        "fallback": {
            "template": ASK_FALLBACK_QUESTION,
            "values": {"concept": "${concept}"},
        },
        # 证据不足时追加"不含引用"说明：追问本身不需要引用，
        # 但必须让学生知道这个问题的依据不是教材（§7.3）。
        # 用 suffix 的"按取值选模板"表达：充足时选到空串，不足时选到那行说明。
        "suffix": {
            "select": "evidence_key",
            "templates": {"true": "", "false": "\n" + ASK_NO_EVIDENCE_NOTE},
            "values": {"evidence_key": "${evidence_sufficient}"},
        },
    },
    # ---------------------------------------------------------------- Teach
    # 必须先拿到可定位证据才允许生成（require_evidence 由决策给出）；
    # 取不到证据 → 用 insufficient_text，不调用模型、不给任何引用（§7.3）；
    # 模型失败 → 用 fallback 的确定性失败表述，同样不附引用。
    ACTION_TEACH: {
        "capability": "generate_grounded",
        "params": {
            "system_prompt": TEACH_SYSTEM_PROMPT,
            "prompt_template": TEACH_PROMPT_TEMPLATE,
            "values": {
                "concept": "${concept}",
                "learning_goal": "${params.learning_goal}",
                "user_input": "${params.user_input}",
                "prior_gap_note": "${params.prior_gap_note}",
                "memory_note": "${params.memory_note}",
            },
            "temperature": GENERATE_TEMPERATURE,
            "max_chars": EVIDENCE_MAX_TEXT_CHARS,
            "metadata": {"graph_node": "teach", "concept": "${concept}"},
        },
        "evidence": _EVIDENCE_BINDING,
        "insufficient_text": {
            "template": TEACH_INSUFFICIENT_EVIDENCE_TEXT,
            "values": {},
        },
        "fallback": {"template": GENERATION_FAILED_TEXT, "values": {}},
    },
    # -------------------------------------------------------------- Correct
    # 与 Teach 同一套纪律：先取证、再生成、失败不附引用（§7.3）。
    # "冲突点怎么提炼"是策略（由节点算好后放进 params.conflicts），
    # "怎么把它写成纠错话术"是能力（本表）。
    ACTION_CORRECT: {
        "capability": "generate_grounded",
        "params": {
            "system_prompt": TEACH_SYSTEM_PROMPT,
            "prompt_template": CORRECT_PROMPT_TEMPLATE,
            "values": {
                "concept": "${concept}",
                "conflicts": "${params.conflicts}",
                "user_input": "${params.user_input}",
                "memory_note": "${params.memory_note}",
            },
            "temperature": GENERATE_TEMPERATURE,
            "max_chars": EVIDENCE_MAX_TEXT_CHARS,
            "metadata": {"graph_node": "correct", "concept": "${concept}"},
        },
        "evidence": _EVIDENCE_BINDING,
        "insufficient_text": {
            "template": CORRECT_INSUFFICIENT_EVIDENCE_TEXT,
            "values": {
                "concept": "${concept}",
                "conflicts": "${params.conflicts}",
            },
        },
        "fallback": {"template": GENERATION_FAILED_TEXT, "values": {}},
    },
    # ------------------------------------------------------------------ Test
    # 出题与评价作答共用一个绑定，差别全部用"按取值选模板"表达：
    #   content_field  按 mode 决定读 Skill 结果的哪个位置（题目 / 评价正文）
    #   prefix / suffix 按 reply_frame（出题 / 判对 / 判错 / 判分缺失）选框架文案
    #   passthrough    把 item_bank_connected 带回决策事件，用于记录"题库未接入"
    #
    # "判分三态怎么措辞"是策略（policies 里的 REPLY_FRAME_* 与 QUIZ_FRAME_*），
    # "把哪些段落拼成回复"是执行，两者在这里接上——Runtime 全程不知道
    # "correct" / "quiz" 是什么意思，只按 ${...} 解析出来的取值查表。
    ACTION_TEST: {
        "capability": "invoke_skill",
        "params": {
            "skill": "quiz",
            "content_field": {
                "select": "mode",
                "templates": {"generate": "item.stem", "evaluate": "evaluation.feedback"},
                "values": {"mode": "${params.mode}"},
            },
            "input": {
                "mode": "${params.mode}",
                "concept": "${concept}",
                "learning_goal": "${params.learning_goal}",
                "difficulty": "${params.difficulty}",
                "item_type": "${params.item_type}",
                "student_answer": "${params.student_answer}",
                "evidence_refs": "${params.evidence_refs}",
            },
            "passthrough": ["item_bank_connected"],
        },
        "fallback": {
            "select": "mode",
            "templates": {
                "generate": QUIZ_FALLBACK_ITEM,
                "evaluate": QUIZ_EVALUATION_FALLBACK,
            },
            # select 的取值必须由 values 提供（模板块渲染时只认 values）
            "values": {"concept": "${concept}", "mode": "${params.mode}"},
        },
        "prefix": {
            "select": "frame",
            "templates": {
                REPLY_FRAME_QUIZ: QUIZ_ITEM_PREFIX,
                REPLY_FRAME_CORRECT: QUIZ_FRAME_CORRECT,
                REPLY_FRAME_INCORRECT: QUIZ_FRAME_INCORRECT,
                REPLY_FRAME_UNKNOWN: QUIZ_FRAME_UNKNOWN,
            },
            "values": {"frame": "${params.reply_frame}"},
        },
        "suffix": {
            "select": "frame",
            "templates": {
                REPLY_FRAME_QUIZ: "\n" + QUIZ_NOT_CONNECTED_NOTE,
                REPLY_FRAME_CORRECT: "\n" + QUIZ_NOT_CONNECTED_NOTE,
                REPLY_FRAME_INCORRECT: "\n" + QUIZ_NOT_CONNECTED_NOTE,
                REPLY_FRAME_UNKNOWN: "\n" + QUIZ_NO_JUDGEMENT_NOTE,
            },
            "values": {"frame": "${params.reply_frame}"},
        },
    },
}

#: 动作 → capability 名字（单看这张表就能回答"这个动作由什么能力做"）
ACTION_CAPABILITIES = {
    action: str(binding.get("capability") or "") for action, binding in ACTION_BINDINGS.items()
}


__all__ = ["ACTION_BINDINGS", "ACTION_CAPABILITIES", "TEACH_PROMPT_TEMPLATE"]
import { useEffect, useState } from "react";

export type Locale = "en" | "zh";

const messages = {
  en: {
    workspace: "Workspace",
    course: "Cognitive Science Tutor",
    overview: "Overview",
    sessions: "Sessions",
    courses: "Courses",
    learning: "Learning",
    library: "Library",
    review: "Review",
    progress: "Progress",
    system: "System",
    providers: "Providers",
    settings: "Settings",
    newSession: "New learning session",
    currentSession: "CURRENT SESSION",
    conceptPractice: "Concept practice",
    startLearning: "Start a learning loop",
    noSession: "No session",
    whatLearn: "What do you want to learn?",
    learningPrompt: "Start with a question. DeepProf will choose the next useful step from your answer.",
    explainConcept: "Explain a concept",
    practiceTest: "Practice and test",
    reviewNotes: "Review notes",
    connected: "Learning session connected. Ask a question or tell me where you are stuck.",
    teachingAction: "teaching action",
    roundStatus: "Round status",
    askPlaceholder: "Ask a question or describe where you are stuck...",
    enterSend: "Enter to send · Shift + Enter for a new line",
    send: "Send →",
    context: "Context",
    learnerState: "Learner state",
    evidenceBuilding: "Evidence incomplete · building",
    currentConcept: "Current concept",
    notSelected: "Not selected",
    learningGoal: "Learning goal",
    understandApply: "Understand and apply core concepts",
    teachingSignal: "Teaching signal",
    everyDecision: "Every decision is recorded so the learning process can be reviewed.",
    pet: "Pet",
    petDriven: "Driven by RuntimeEvent",
    openPet: "Open pet",
    openWorkspace: "Open DeepProf workspace",
    startup: "Starting DeepProf Runtime...",
    offline: "Offline mode",
    firstRun: "FIRST RUN",
    connectModel: "Connect your learning model",
    noAccount: "DeepProf does not create an account. Save one provider profile to start a local learning workspace.",
    credential: "Credential",
    saveTest: "Save and test connection →",
    privacy: "The credential crosses the controlled IPC boundary into the system secret store. It is never placed in Renderer logs, events, or page state.",
    providerSettings: "PROVIDER SETTINGS",
    modelConnections: "Model connections",
    closeSettings: "Close settings",
    addProvider: "＋ Add provider",
    credentialSaved: "Credential saved",
    credentialMissing: "Credential missing",
    profileId: "Profile ID",
    displayName: "Display name",
    baseUrl: "Base URL",
    defaultModel: "Default model",
    discoverModels: "Discover models",
    testHealth: "Test health",
    availableModels: "Available models",
    cancel: "Cancel",
    saveProvider: "Save provider",
    working: "Working...",
    savedProbe: "Saved. Run a probe to verify the provider.",
    modelsFound: "model(s) discovered",
    language: "中文",
    petWaitingInput: "Waiting for input",
    petSessionReady: "Session ready",
    petProcessing: "Processing",
    petWaitingApproval: "Waiting for approval",
    petCorrect: "Correct!",
    petReview: "Let's review this",
    petEvidence: "Evidence is ready",
    petProblem: "This step needs attention",
    petClickThrough: "Click-through",
  },
  zh: {
    workspace: "工作台",
    course: "认知科学导师",
    overview: "概览",
    sessions: "会话",
    courses: "课程",
    learning: "学习",
    library: "资料库",
    review: "复习",
    progress: "进度",
    system: "系统",
    providers: "模型接入",
    settings: "设置",
    newSession: "新建学习会话",
    currentSession: "当前会话",
    conceptPractice: "概念练习",
    startLearning: "开始一轮学习",
    noSession: "未选择会话",
    whatLearn: "今天想学什么？",
    learningPrompt: "从一个问题开始，DeepProf 会根据你的回答决定下一步。",
    explainConcept: "理解一个概念",
    practiceTest: "练习与测验",
    reviewNotes: "复习资料",
    connected: "学习会话已连接。可以直接提问，或告诉我你卡在哪里。",
    teachingAction: "教学动作",
    roundStatus: "本轮状态",
    askPlaceholder: "提问，或描述你现在遇到的困难……",
    enterSend: "Enter 发送 · Shift + Enter 换行",
    send: "发送 →",
    context: "上下文",
    learnerState: "学习者状态",
    evidenceBuilding: "证据不足 · 正在建立",
    currentConcept: "当前概念",
    notSelected: "未选择",
    learningGoal: "学习目标",
    understandApply: "理解并应用核心概念",
    teachingSignal: "教学信号",
    everyDecision: "每一步决策都会被记录，方便回看学习过程。",
    pet: "桌宠",
    petDriven: "由 RuntimeEvent 驱动",
    openPet: "打开桌宠",
    openWorkspace: "打开 DeepProf 工作台",
    startup: "正在启动 DeepProf Runtime……",
    offline: "离线模式",
    firstRun: "首次运行",
    connectModel: "连接你的学习模型",
    noAccount: "DeepProf 不创建账号。保存一份 Provider 配置，即可开始本地学习工作台。",
    credential: "访问凭据",
    saveTest: "保存并测试连接 →",
    privacy: "凭据只通过受控 IPC 写入系统凭据库，不会进入 Renderer 日志、事件或页面状态。",
    providerSettings: "模型接入设置",
    modelConnections: "模型连接",
    closeSettings: "关闭设置",
    addProvider: "＋ 添加 Provider",
    credentialSaved: "凭据已保存",
    credentialMissing: "缺少凭据",
    profileId: "配置 ID",
    displayName: "显示名称",
    baseUrl: "Base URL",
    defaultModel: "默认模型",
    discoverModels: "发现模型",
    testHealth: "测试健康状态",
    availableModels: "可用模型",
    cancel: "取消",
    saveProvider: "保存 Provider",
    working: "处理中……",
    savedProbe: "已保存。运行健康探测以验证 Provider。",
    modelsFound: "个模型已发现",
    language: "English",
    petWaitingInput: "等待输入",
    petSessionReady: "会话已准备好",
    petProcessing: "正在处理",
    petWaitingApproval: "等待审批",
    petCorrect: "答对了！",
    petReview: "一起复习这一题",
    petEvidence: "证据已准备好",
    petProblem: "这一步需要关注",
    petClickThrough: "点击穿透",
  },
} as const;

export type CopyKey = keyof typeof messages.en;
export type Translator = (key: CopyKey) => string;

export function translate(locale: Locale, key: CopyKey): string {
  return messages[locale][key] || messages.en[key];
}

function readLocale(): Locale {
  return localStorage.getItem("deepprof.locale") === "zh" ? "zh" : "en";
}

export function useLocale(): { locale: Locale; setLocale: (locale: Locale) => void; t: Translator } {
  const [locale, setCurrentLocale] = useState<Locale>(readLocale);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === "deepprof.locale") setCurrentLocale(event.newValue === "zh" ? "zh" : "en");
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function setLocale(next: Locale): void {
    localStorage.setItem("deepprof.locale", next);
    setCurrentLocale(next);
  }

  return { locale, setLocale, t: (key) => translate(locale, key) };
}

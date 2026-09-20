/**
 * preload —— 渲染进程与主进程之间唯一的桥
 *
 * 安全原则（对应验收标准）：
 *   1. 只暴露白名单方法，绝不把裸 ipcRenderer 交给页面
 *   2. 渲染进程拿不到任何模型密钥 —— 密钥只存在于主进程
 *   3. contextIsolation 已在主进程开启
 */

const { contextBridge, ipcRenderer } = require('electron')

const EVENT_CHANNEL = 'runtime:event'
const WALK_CHANNEL = 'pet:walk'

contextBridge.exposeInMainWorld('deepprof', {
  /**
   * 发一条消息。
   * @param {string} text 用户输入
   * @param {string} scenario 演示场景：normal | loading | error | reconnect
   */
  sendMessage: (text, scenario) => ipcRenderer.invoke('session:send', text, scenario),

  /** 取消当前这一轮 */
  cancel: () => ipcRenderer.invoke('session:cancel'),

  /** 模拟断线重连 */
  reconnect: () => ipcRenderer.invoke('session:reconnect'),

  /**
   * 订阅统一事件流 —— 渲染层唯一的事件入口。
   * 返回取消订阅函数。
   */
  onEvent: (handler) => {
    const listener = (_e, evt) => handler(evt)
    ipcRenderer.on(EVENT_CHANNEL, listener)
    return () => ipcRenderer.removeListener(EVENT_CHANNEL, listener)
  },

  /** 订阅「正在走动」状态，用来播走路动画 */
  onWalk: (handler) => {
    const listener = (_e, walking) => handler(walking)
    ipcRenderer.on(WALK_CHANNEL, listener)
    return () => ipcRenderer.removeListener(WALK_CHANNEL, listener)
  },

  /**
   * 语音合成（神经语音）。
   *
   * 为什么合成要放主进程：渲染进程不该有起子进程和读写文件的能力
   * （contextIsolation + nodeIntegration:false 就是为了这个）。
   * 这里只转发一句文本，拿回一段音频。
   *
   * ⚠️ 合成失败时**不抛异常**，而是返回 { ok:false, reason }——
   *    前端据此退回 Chromium 内置的系统合成音。对方机器上没装 Python、
   *    没装 edge-tts、或者没网，桌宠都必须还能说话。
   */
  tts: {
    synthesize: (text) => ipcRenderer.invoke('tts:synthesize', text),
    /** 自检：神经语音到底能不能用 */
    selftest: () => ipcRenderer.invoke('tts:selftest')
  },

  /**
   * 导出「经用户授权的偏好更新」（任务书 §16.4 交接要求）。
   * 后端端点还没有，先落成 JSON 文件到桌面 `DeepProf_偏好更新/`。
   */
  exportPreference: (data) => ipcRenderer.invoke('pref:export', data),

  /** 窗口控制（最小集合） */
  win: {
    /**
     * 开关"强制穿透"（用户手动）。
     * 自动穿透的决策在主进程的轮询循环里，这里只是改意图标记。
     */
    setIgnoreMouse: (ignore) => ipcRenderer.invoke('win:set-ignore-mouse', ignore),
    /**
     * 上报命中区：角色画在窗口里的矩形 + 64×64 alpha 掩码。
     * 主进程靠它判断"光标是不是真在角色身上"，从而决定要不要穿透。
     */
    setHitRegion: (r) => ipcRenderer.invoke('win:set-hit-region', r),
    /**
     * 菜单/面板的展开状态 **+ 它实际占的那块矩形**（窗口坐标系）。
     * 只报 true/false 的话主进程只能"整窗口不穿透"，面板以外那圈透明区会跟着挡鼠标；
     * 带上矩形它才能只挡该挡的地方。矩形量不出来就传 null（主进程会退回整窗口不穿透）。
     */
    setUiOpen: (v, rect) => ipcRenderer.invoke('win:set-ui-open', v, rect),
    /** 拖动：按增量移动窗口 */
    moveBy: (dx, dy) => ipcRenderer.invoke('win:move-by', dx, dy),
    /** 告诉主进程拖动开始/结束，期间暂停漫游 */
    dragState: (dragging) => ipcRenderer.invoke('win:drag-state', dragging),
    /** 展开 / 收起聊天面板（窗口会跟着变大变小） */
    setChatOpen: (open) => ipcRenderer.invoke('win:set-chat-open', open),
    /** 开关自动漫游 */
    setRoaming: (on) => ipcRenderer.invoke('win:set-roaming', on),
    /** 立刻随便走走 */
    wander: () => ipcRenderer.invoke('win:wander'),
    /** 把小人叫回屏幕底边（找不回来时用） */
    home: () => ipcRenderer.invoke('win:home'),
    /** 置顶开关 */
    setAlwaysOnTop: (on) => ipcRenderer.invoke('win:set-always-on-top', on),
    /** 退出应用 */
    quit: () => ipcRenderer.invoke('win:quit')
  }
})

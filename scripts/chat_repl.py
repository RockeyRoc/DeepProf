"""DeepProf 命令行入口（CLI）—— 交互式聊天 / 换模型 / 换音色 / 开桌宠。

═══ 为什么要有这个（2026-09-21）═══

用户的原话：「启动时桌面会弹出黑窗口，我建议改成像 claude 那样的 cli，还支持换模型」。

原来的启动方式是一个 `.bat`：`chcp 65001` + `cd` + `npm run dev` + `pause`。
它必然带出一个控制台窗口，而且 `npm run dev` 之后控制台里滚的是 Vite 的日志 ——
桌宠是"桌面上的一个小人"，却要靠一个开发服务器来启动，这本身就不对。

现在：**命令行是唯一入口**。

    deepprof            # 进入交互（聊天 / 换模型 / 换音色）
    deepprof /pet       # 直接唤起桌宠
    deepprof --fake     # 不联网，用 FakeProvider 验证链路

进到交互里之后 `/pet` 也能唤起桌宠。桌宠自己会拉起后端（见
`desktop/src/main/index.js` 的后端监督器），所以这里只负责把 Electron 起起来。

═══ 与桌宠共享的配置 ═══

`~/.deepprof/config.json`  —— 模型选择（llm_provider / llm_model）
`~/.deepprof/voice.json`   —— 音色（engine / voice / rate / pitch）

这两个文件桌宠那边也在读写（同一个目录、同一份格式），所以在 CLI 里换的模型
和音色，桌宠下次启动就会用上 —— 不是两套互不相干的设置。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

# 让脚本能从项目根目录导入包（scripts/ 不是 Python 包）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from runtime.core.errors import ProviderError  # noqa: E402
from runtime.providers.factory import get_provider  # noqa: E402
from runtime.service import RuntimeService  # noqa: E402

#: 与桌宠主进程共用同一个数据目录（`DEEPPROF_HOME` 可覆盖）
HOME = Path(os.environ.get("DEEPPROF_HOME") or (Path.home() / ".deepprof"))
CONFIG_PATH = HOME / "config.json"
VOICE_PATH = HOME / "voice.json"

PROVIDERS = ("deepseek", "openai", "qwen", "fake")

#: provider → 覆盖模型名时要注入的环境变量（settings.py 就是按这些名字读的）
MODEL_ENV = {
    "deepseek": "DEEPSEEK_MODEL",
    "openai": "OPENAI_MODEL",
    "qwen": "QWEN_MODEL",
}

VOICE_DEFAULTS = {
    "engine": "edge-tts",
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": "-6%",
    "pitch": "+3Hz",
}


# ================= 配置文件（与桌宠共用） =================
def read_json(path: Path, fallback: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**fallback, **data} if isinstance(data, dict) else dict(fallback)
    except Exception:
        return dict(fallback)


def write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as exc:  # 写不进去不该让 CLI 挂掉
        print(f"[配置] 写入失败 {path}：{exc}")
        return False


# ================= 桌宠 =================
def launch_pet() -> None:
    """唤起桌宠（Electron）。桌宠自己会拉起 Python 后端，这里只负责启动窗口。"""
    desktop = _ROOT / "desktop"
    exe_name = "electron.exe" if os.name == "nt" else "electron"
    exe = desktop / "node_modules" / "electron" / "dist" / exe_name

    if not exe.exists():
        print("[桌宠] 找不到 Electron。请先在 desktop/ 目录执行：npm install")
        return
    if not (desktop / "out" / "main" / "index.js").exists():
        print("[桌宠] 还没构建。请先在 desktop/ 目录执行：npm run build")
        return

    # ⚠️ 两个 flag 都是为了"不弹黑窗口"（用户反馈的第 5 条）：
    #    DETACHED_PROCESS      —— 子进程不继承/不新建控制台
    #    CREATE_NEW_PROCESS_GROUP —— 关掉终端时桌宠不会跟着被杀（它是常驻的小人）
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        subprocess.Popen(
            [str(exe), "."],
            cwd=str(desktop),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:
        print(f"[桌宠] 启动失败：{exc}")
        return
    print("[桌宠] 已唤起（它会在后台自己拉起后端，第一次约需十几秒）。")


# ================= 音色 =================
def list_zh_voices() -> list[dict] | None:
    """列出中文音色；没装 edge-tts 或没网时返回 None。"""
    try:
        import edge_tts
    except ImportError:
        print("[音色] 没装 edge-tts。执行：pip install edge-tts")
        return None
    try:
        voices = asyncio.run(edge_tts.list_voices())
    except Exception as exc:
        print(f"[音色] 取音色表失败（需要联网）：{exc}")
        return None
    zh = [
        {
            "name": v.get("ShortName", ""),
            "gender": v.get("Gender", ""),
            "friendly": (v.get("FriendlyName") or "").split(" - ")[0],
        }
        for v in voices
        if str(v.get("Locale", "")).lower().startswith("zh")
    ]
    return sorted(zh, key=lambda x: x["name"])


# ================= 帮助 =================
HELP = """可用命令：
  /help              显示这份帮助
  /model             列出可选模型（provider），标出当前用的
  /model <名字>      切换模型，如 /model qwen
  /model <名字> <模型>  切换并覆盖具体模型名，如 /model deepseek deepseek-chat
  /voice             列出可选音色（需要联网）
  /voice <音色名>     换音色，如 /voice zh-CN-YunxiNeural
  /pet               唤起桌面宠物（桌宠自己会拉起后端）
  /status            显示当前 provider / 会话 / 音色
  /clear             重开会话（轨迹仍落盘，可用 service.replay 复盘）
  /quit              退出（也可以直接输入 quit / exit / Ctrl-C）
其余输入一律当作聊天内容。"""


# ================= 主循环 =================
async def repl(use_fake: bool) -> None:
    saved = read_json(CONFIG_PATH, {})
    provider_name = "fake" if use_fake else str(saved.get("llm_provider") or "")
    model_override = "" if use_fake else str(saved.get("llm_model") or "")

    def build() -> tuple[RuntimeService | None, str]:
        """按当前 provider/模型 建一个 RuntimeService；失败返回 (None, 原因)。"""
        try:
            if provider_name == "fake" or not provider_name:
                prov = get_provider(provider_name or "")
            elif model_override:
                prov = get_provider(provider_name, model=model_override)
            else:
                prov = get_provider(provider_name)
        except (ProviderError, ValueError) as exc:
            return None, str(exc)
        return RuntimeService(provider=prov), ""

    service, err = build()
    if service is None:
        print(f"[启动失败] {err}")
        print("请在项目根目录的 .env 里填入 API key，或先用 --fake 验证链路。")
        return

    session = service.create_session(learner_id="local")

    print("DeepProf CLI —— 桌面宠物与教学链路的统一入口")
    print(f"  provider = {service.provider.name}" + (f"（模型 {model_override}）" if model_override else ""))
    print(f"  session  = {session.session_id}")
    print(f"  数据目录 = {HOME}")
    print("输入 /help 看命令，/quit 退出。")
    print("-" * 56)

    while True:
        try:
            user = (await asyncio.to_thread(input, "你 > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user:
            continue

        # ---------- 命令 ----------
        if user.lower() in ("quit", "exit", "/quit", "/exit"):
            break

        if user.lower() in ("clear", "/clear"):
            service.close_session(session)
            session = service.create_session(learner_id="local")
            print(f"（已重开会话 {session.session_id}）")
            continue

        if user in ("/help", "/?"):
            print(HELP)
            continue

        if user.startswith("/model"):
            parts = user.split()
            if len(parts) == 1:
                print("可选模型：")
                for p in PROVIDERS:
                    mark = " ← 当前" if p == (provider_name or "deepseek") else ""
                    print(f"  {p}{mark}")
                continue
            name = parts[1].lower()
            if name not in PROVIDERS:
                print(f"[模型] 未知 provider：{name}（可选 {', '.join(PROVIDERS)}）")
                continue
            new_model = parts[2] if len(parts) > 2 else ""

            # ⚠️ 覆盖模型名要走环境变量：settings.py 是按 <PROVIDER>_MODEL 读的，
            #    只改内存里的 provider 对象、不写环境变量的话，
            #    桌宠那边（另一个进程）读到的还是 .env 里的旧值。
            if new_model and name in MODEL_ENV:
                os.environ[MODEL_ENV[name]] = new_model

            old_provider, old_model = provider_name, model_override
            provider_name, model_override = name, new_model
            new_service, err = build()
            if new_service is None:
                provider_name, model_override = old_provider, old_model
                print(f"[模型] 切换失败：{err}")
                continue

            service.close_session(session)
            service = new_service
            session = service.create_session(learner_id="local")
            write_json(CONFIG_PATH, {"llm_provider": provider_name, "llm_model": model_override})
            print(f"（已切到 {provider_name}"
                  + (f" / {model_override}" if model_override else "")
                  + f"，新会话 {session.session_id}；桌宠下次启动也会用这个）")
            continue

        if user.startswith("/voice"):
            parts = user.split(maxsplit=1)
            if len(parts) == 1:
                voices = list_zh_voices()
                if voices is None:
                    continue
                current = read_json(VOICE_PATH, VOICE_DEFAULTS).get("voice")
                print("可选中文音色：")
                for v in voices:
                    mark = " ← 当前" if v["name"] == current else ""
                    gender = {"Female": "女", "Male": "男"}.get(v["gender"], "")
                    print(f"  {v['name']:<28}{gender}{mark}")
                continue
            name = parts[1].strip()
            cfg = read_json(VOICE_PATH, VOICE_DEFAULTS)
            cfg["voice"] = name
            if write_json(VOICE_PATH, cfg):
                print(f"（音色已换成 {name}；桌宠下一条回复就用新音色）")
            continue

        if user == "/pet":
            launch_pet()
            continue

        if user == "/status":
            voice = read_json(VOICE_PATH, VOICE_DEFAULTS)
            print(f"  provider = {service.provider.name}")
            print(f"  模型覆盖 = {model_override or '（用 .env 默认）'}")
            print(f"  session  = {session.session_id}")
            print(f"  音色     = {voice.get('voice')}（{voice.get('rate')} / {voice.get('pitch')}）")
            print(f"  数据目录 = {HOME}")
            continue

        if user.startswith("/"):
            print(f"[命令] 不认识：{user}。输入 /help 看可用命令。")
            continue

        # ---------- 聊天 ----------
        print("DeepProf > ", end="", flush=True)
        try:
            async for chunk in service.run_turn(session, user):
                if chunk.kind == "delta":
                    print(chunk.text, end="", flush=True)
                elif chunk.kind == "tool":
                    mark = "成功" if chunk.tool_ok else "失败"
                    print(f"\n[工具 {chunk.tool_name} {mark}] ", end="", flush=True)
                elif chunk.kind == "error":
                    print(f"\n[失败] {chunk.error}")
        except ProviderError as exc:
            print(f"\n[调用失败] {exc}")
        print()

    service.close_session(session)
    print("会话已结束（轨迹已落盘，可用 service.replay 复盘）。")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deepprof",
        description="DeepProf 命令行入口：聊天 / 换模型 / 换音色 / 开桌宠",
    )
    parser.add_argument("--fake", action="store_true", help="使用 FakeProvider，不联网")
    parser.add_argument(
        "command",
        nargs="?",
        default="",
        help="直接执行的命令，如 /pet（不给就进交互）",
    )
    args = parser.parse_args()

    # `deepprof /pet` —— 不起交互，直接唤起桌宠就退出
    if args.command.strip() == "/pet":
        launch_pet()
        return

    try:
        asyncio.run(repl(args.fake))
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()

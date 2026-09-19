# -*- coding: utf-8 -*-
"""edge-tts 语音合成 —— 给桌宠用的命令行外壳。

═══ 为什么需要这个 ═══

前端能直接用的只有 Chromium 内置的 Web Speech API，它走的是 Windows 的 SAPI 音源，
最年轻的也是十几年前的东西，念出来像客服播报 —— **语调很平**。

项目本来的选型就是 Edge-TTS，音色是微软的**神经网络语音**（晓晓等），
自然度高一个数量级。这个脚本就是那座桥：主进程调它合成 mp3，前端拿去播。

═══ 用法 ═══

    python say.py --text-file <utf8文本文件> --out <输出.mp3>
    python say.py --selftest          # 自检：edge-tts 装了没、能不能出声

⚠️ 文本走**文件**而不是命令行参数：回复里可能有引号、换行、特殊字符，
   走命令行迟早被转义问题坑到。主进程负责写临时文件。

成功时 stdout 打印 `OK <路径>`，失败打印 `ERR <原因>` 并返回非 0 退出码。
主进程和前端都靠这个判断要不要退回系统合成音。
"""

import argparse
import asyncio
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ⚠️ 语调参数 —— 和 tts.js 里的 SPEECH_STYLE 是一回事，改的时候两处一起看。
#    rate 略慢显得稳、显得不着急；pitch 抬一点贴合年轻女声，抬多了就假。
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "-6%"    # 比正常慢 6%
DEFAULT_PITCH = "+3Hz"  # 微微抬一点点


def selftest():
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print("ERR edge-tts 没装（pip install edge-tts）")
        return 2

    async def probe():
        # 真合成一小段，确认不只是"包在"，而是**真的能出声**（还要能连上微软的端点）
        c = edge_tts.Communicate("测试", DEFAULT_VOICE)
        n = 0
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                n += len(chunk["data"])
        return n

    try:
        n = asyncio.run(probe())
    except Exception as e:
        print("ERR 合成失败：%s" % e)
        return 3
    if n <= 0:
        print("ERR 合成出来了但是空音频")
        return 4
    print("OK 可用，试合成得到 %d 字节音频" % n)
    return 0


def main():
    ap = argparse.ArgumentParser(description="edge-tts 合成外壳")
    ap.add_argument("--text-file", help="UTF-8 文本文件路径")
    ap.add_argument("--text", help="直接给文本（短句用，特殊字符多的话别用）")
    ap.add_argument("--out", help="输出 mp3 路径")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--rate", default=DEFAULT_RATE)
    ap.add_argument("--pitch", default=DEFAULT_PITCH)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.out:
        print("ERR 缺少 --out")
        return 1

    if args.text_file:
        try:
            with open(args.text_file, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print("ERR 读不到文本文件：%s" % e)
            return 1
    else:
        text = args.text or ""

    text = text.strip()
    if not text:
        print("ERR 文本是空的")
        return 1

    try:
        import edge_tts
    except ImportError:
        print("ERR edge-tts 没装")
        return 2

    async def run():
        c = edge_tts.Communicate(text, args.voice, rate=args.rate, pitch=args.pitch)
        await c.save(args.out)

    try:
        asyncio.run(run())
    except Exception as e:
        print("ERR 合成失败：%s" % e)
        return 3

    if not os.path.exists(args.out) or os.path.getsize(args.out) == 0:
        print("ERR 输出文件是空的")
        return 4

    print("OK %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

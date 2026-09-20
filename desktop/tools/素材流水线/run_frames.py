# -*- coding: utf-8 -*-
"""动作帧流水线入口（**ASCII 文件名，专给 .bat 调用**）。

═══ 为什么不直接让 .bat 去调中文名的脚本 ═══

cmd.exe 执行批处理是**按字节偏移**往下读的，文件里只要出现非 ASCII 字符
（哪怕是中文文件名），配合 chcp 换代码页就可能让它偏移错位，把行碎片当成
命令执行 —— 报一堆「不是内部或外部命令」，连最后的 pause 都会被吃掉，
用户看到的就是**黑窗口一闪就没了**。

所以 .bat 保持纯 ASCII，中文名的脚本由这个 ASCII 入口去调 —— Python 处理
Unicode 文件名毫无问题，坑只存在于 bat 那一层。

用法（一般由 .bat 调用，也可直接跑）：
    python run_frames.py            # 处理全部动作
    python run_frames.py walk       # 只处理 walk
"""

import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# 顺序不能反：先归一化，再预览
STEPS = ["归一化帧.py", "预览.py"]


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    args = [action] if action else []

    for script in STEPS:
        path = os.path.join(HERE, script)
        if not os.path.exists(path):
            print("✗ 找不到脚本：%s" % path)
            return 1

        # flush=True 很关键：子进程是直接往终端写的，而这里的 print 是带缓冲的，
        # 不刷的话 banner 会跑到子进程输出的【后面】，看起来像顺序错了。
        print("\n" + "=" * 52, flush=True)
        print("  运行 %s %s" % (script, action or "(全部)"), flush=True)
        print("=" * 52, flush=True)

        r = subprocess.run([sys.executable, path] + args)
        if r.returncode != 0:
            print("\n✗ %s 失败了（返回码 %d），后面的步骤不再执行。" % (script, r.returncode))
            return r.returncode

    # 打开预览目录。
    # ⚠️ 这件事必须在 Python 里做，不能写进 .bat ——
    #    .bat 必须是纯 ASCII（见文件头说明），而"动作帧\预览"是中文路径。
    preview = os.path.join(HERE, "动作帧", "预览")
    if os.path.isdir(preview):
        print("\n打开预览目录：%s" % preview)
        try:
            os.startfile(preview)
        except Exception as e:
            print("（打不开资源管理器：%s，手动打开上面这个路径就行）" % e)
    else:
        print("\n⚠️ 预览目录还不存在：%s" % preview)

    return 0


if __name__ == "__main__":
    sys.exit(main())

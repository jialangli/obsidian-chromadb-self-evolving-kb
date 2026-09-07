"""
Knowledge KB API 守护进程 (api_guard)
====================================
常驻后台，周期性探测 8300 端口：
  - 端口未监听 → 拉起 kb_api_server.py 子进程
  - 端口已监听 → 跳过（幂等，不重复启动）
  - 单实例：文件锁防止多个守护同时运行

部署：由 kb_api_autostart.vbs 在 Windows 登录时以隐藏窗口启动，
     放入 启动文件夹 即可实现「开机自启 + 崩溃自愈」。

用法：
  python kb_api_guard.py                # 默认 8300，每 30 秒探测
  python kb_api_guard.py --port 8300 --interval 30
"""

import argparse
import msvcrt
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # D:\kb-engine
LOG_DIR = BASE / "logs"
LOG = LOG_DIR / "api_guard.log"
LOCK = LOG_DIR / "api_guard.lock"
SERVER_SCRIPT = Path(__file__).resolve().parent / "kb_api_server.py"

PYTHON = sys.executable


# ── 单实例锁 ──────────────────────────────────────────
# 注意：锁句柄必须是全局变量！若在函数内局部持有，函数返回后句柄被回收，
#       Windows 文件锁会随句柄关闭而自动释放，导致锁完全失效。
_lock_file = None


def acquire_lock() -> bool:
    """获取文件锁；已有守护在跑则返回 False。进程退出后锁自动释放。"""
    global _lock_file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+")
    try:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(" ")  # 确保文件至少 1 字节，锁才能落在有效区域
            f.flush()
        f.seek(0)  # 锁必须从文件开头起 1 字节，否则越界不互斥
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # 非阻塞锁 1 字节
    except OSError:
        f.close()
        return False
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    _lock_file = f  # 全局持有，防止句柄回收导致锁释放
    return True


def log(msg: str):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except OSError:
        pass


def port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def launch_server(port: int):
    """拉起 API 服务子进程（隐藏窗口，无控制台输出）"""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [PYTHON, str(SERVER_SCRIPT), "--port", str(port)],
        cwd=str(SERVER_SCRIPT.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return proc.pid


def main():
    parser = argparse.ArgumentParser(description="Knowledge KB API 守护进程")
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument("--interval", type=int, default=30, help="探测间隔（秒）")
    args = parser.parse_args()

    if not acquire_lock():
        log(f"guard 已在运行（锁文件 {LOCK}），本实例退出")
        sys.exit(0)

    log(f"=== guard 启动 port={args.port} interval={args.interval}s ===")
    prev = None
    while True:
        ok = port_open(args.port)
        if ok != prev:
            if ok:
                log(f"端口 {args.port} 已监听，服务正常")
            else:
                log(f"端口 {args.port} 未监听，拉起 API 服务…")
                try:
                    pid = launch_server(args.port)
                    log(f"已拉起 kb_api_server pid={pid}")
                except Exception as e:
                    log(f"拉起失败: {e}")
        prev = ok
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

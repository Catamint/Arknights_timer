"""Historical V1 web console, retained only as a behavior reference."""

import argparse
import time
import webbrowser

from ark_emulator import Simulator
from ark_emulator.live_server import LiveServer


def main():
    parser = argparse.ArgumentParser(description="V1 历史网页参考；当前模拟器底座为 ark_sim V2")
    parser.add_argument("--level", default="level_main_00-01",
                        help="initial level id")
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    print("[V1 历史参考] 本入口用于查阅旧行为；当前开发入口为 python -m ark_sim。")

    sim = Simulator(level_id=args.level)
    server = LiveServer(sim, port=args.port, speed=1.0)
    try:
        server.start()
    except OSError as exc:
        raise SystemExit(
            "端口 %d 已被另一个网页模拟器占用，请先关闭旧窗口/进程，"
            "或使用 --port 指定其他端口。\n%s" % (args.port, exc))
    url = "http://127.0.0.1:%d/" % args.port
    print("Ark_emulator web console:", url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()

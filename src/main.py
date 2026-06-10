import argparse
import time
from pathlib import Path

import yaml

from . import filter as kw_filter
from .scraper import run_once

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="持续循环抓取")
    ap.add_argument("--config", default=str(ROOT / "config" / "sources.yaml"))
    ap.add_argument("--cookies", default=str(ROOT / "cookies" / "tieba.json"))
    ap.add_argument("--db", default=str(ROOT / "data" / "radar.db"))
    args = ap.parse_args()

    cfg_all = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    forums = cfg_all["forums"]
    crawl_cfg = cfg_all["crawl"]
    cookie_path = Path(args.cookies)
    db_path = Path(args.db)

    if not cookie_path.exists():
        raise SystemExit(f"找不到 cookie 文件：{cookie_path}\n参考 cookies/README.md 导出后再跑。")

    while True:
        run_once(forums, crawl_cfg, db_path, cookie_path)
        # 关键词预筛
        from .db import connect as db_connect
        conn = db_connect(db_path)
        n_signal = kw_filter.run(conn)
        conn.close()
        print(f"[filter] 新标记 signal={n_signal}")
        if not args.loop:
            return
        interval = crawl_cfg["loop_interval_seconds"]
        print(f"[sleep] {interval}s 后下一轮")
        time.sleep(interval)


if __name__ == "__main__":
    main()

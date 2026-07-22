"""HSys 采集启动器（配置驱动版 StreakHunter）。

从 config.json 读取账号 / 打码 token / 入口 URL / 路径 / 采集参数，
注入 scripts/streak_hunter 后启动采集。与原脚本的差别只在配置来源，
采集逻辑完全复用，不重写。

用法（在仓库根目录）：
    uv run python hsys/crawl-bot/hunter.py                 # 按 config.json 启动
    uv run python hsys/crawl-bot/hunter.py --check         # 只校验并打印配置摘要
    uv run python hsys/crawl-bot/hunter.py --min 6         # 临时覆盖连胜阈值

⚠ config.json 含有账号密码与打码 token，已加入 .gitignore，严禁提交。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # 仓库根 HData/
sys.path.insert(0, str(ROOT))

from loguru import logger                          # noqa: E402

import scripts.streak_hunter as sh                 # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"

# config.json 中允许出现的键（出现未知键直接报错，防止拼错后静默用默认值）
KNOWN_KEYS = {
    "accounts", "geepass_token", "jfbym_token", "entry_url",
    "db_path", "log_file", "min_streak", "max_accounts",
    "proxies", "proxy_cap", "purge_raw_days", "push",
}


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"配置文件不存在: {path}\n"
                 f"请从 config.example.json 复制一份并填入真实值。")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(cfg) - KNOWN_KEYS
    if unknown:
        sys.exit(f"配置含未知键 {sorted(unknown)}，"
                 f"允许键: {sorted(KNOWN_KEYS)}")
    if not cfg.get("accounts"):
        sys.exit("配置缺少 accounts（至少 2 个：1 发现 + N 监控）")
    for i, a in enumerate(cfg["accounts"]):
        if "account" not in a or "password" not in a:
            sys.exit(f"accounts[{i}] 缺 account/password 字段")
    return cfg


def _abs(p: str) -> str:
    """配置里的相对路径一律按仓库根解析。"""
    q = Path(p)
    return str(q if q.is_absolute() else ROOT / q)


def main():
    ap = argparse.ArgumentParser(description="HSys 采集启动器（配置驱动）")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="配置文件路径（默认 hsys/crawl-bot/config.json）")
    ap.add_argument("--min", type=int, default=0, dest="min_streak",
                    help="覆盖配置里的连胜入场阈值")
    ap.add_argument("--check", action="store_true",
                    help="只校验配置并打印摘要，不启动采集")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))

    # ── 注入采集脚本的全局配置 ──
    sh.ACCOUNTS = cfg["accounts"]
    if cfg.get("geepass_token"):
        sh.GEEPASS = cfg["geepass_token"]
    if cfg.get("jfbym_token"):
        sh.JFBYM = cfg["jfbym_token"]
    if cfg.get("entry_url"):
        sh.ENTRY_URL = cfg["entry_url"]

    min_streak = args.min_streak or int(cfg.get("min_streak", 4))
    db_path = _abs(cfg.get("db_path", "hsys/crawl-bot/data/streak.db"))
    log_file = _abs(cfg.get("log_file", "hsys/crawl-bot/logs/hunter.log"))
    proxies = _abs(cfg["proxies"]) if cfg.get("proxies") else ""
    proxy_cap = int(cfg.get("proxy_cap", 10))
    max_accounts = int(cfg.get("max_accounts", 0))
    purge_days = int(cfg.get("purge_raw_days", 7))

    n_disc = 1
    n_mon = (min(max_accounts, len(sh.ACCOUNTS) - 1)
             if max_accounts > 0 else len(sh.ACCOUNTS) - 1)
    summary = {
        "config": str(Path(args.config).resolve()),
        "entry_url": sh.ENTRY_URL,
        "accounts": f"{n_disc} 发现 + {n_mon} 监控",
        "min_streak": min_streak,
        "db_path": db_path,
        "log_file": log_file,
        "proxies": proxies or "(本机直连)",
        "proxy_cap": proxy_cap if proxies else "-",
        "purge_raw_days": ("永不删除" if purge_days <= 0
                          else f"{purge_days} 天（超窗应先跑 archive.py 归档）"),
        "push": cfg.get("push", {}),
    }
    if args.check:
        print("配置校验通过：")
        for k, v in summary.items():
            print(f"  {k:16s} = {v}")
        return

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | {message}")
    logger.add(log_file, rotation="50 MB", level="DEBUG", encoding="utf-8")
    logger.info(f"HSys 采集启动（配置驱动）| {summary}")
    try:
        asyncio.run(sh.amain(min_streak, db_path, max_accounts,
                             proxies, proxy_cap, purge_days))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

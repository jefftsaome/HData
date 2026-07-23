"""HSys 采集程序单入口（crawl-bot）。

一个入口脚本 + --strategy 选择采集内容；策略实现于 strategies/ 目录，
只依赖 hdata 包与本目录 store.py，不调用 HData 仓库 scripts/ 下任何脚本。

用法（仓库根目录）：
    uv run python hsys/crawl-bot/main.py                        # 默认 streak 策略
    uv run python hsys/crawl-bot/main.py --strategy streak --min 5
    uv run python hsys/crawl-bot/main.py --check                # 只校验配置

新增采集策略：在 strategies/ 下加模块，暴露 STRATEGY_INFO /
register_args(ap) / run(cfg, args)，并在下方 STRATEGIES 注册。

⚠ config.json 含账号密码与打码 token，已 gitignore，严禁提交。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent          # hsys/crawl-bot
ROOT = BOT_DIR.parent.parent                       # 仓库根（hdata 包所在）
for p in (str(ROOT), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from loguru import logger                          # noqa: E402

# 策略注册表：名称 → (模块路径, 说明)
STRATEGIES = {
    "streak": ("strategies.streak", "长龙采集：连胜桌监控到断龙/删失"),
}

# config.json 允许的键（出现未知键直接报错，防拼错静默用默认值）
KNOWN_KEYS = {
    "strategy",                    # 配置里默认策略名（命令行 --strategy 优先）
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
        sys.exit(f"配置含未知键 {sorted(unknown)}，允许键: {sorted(KNOWN_KEYS)}")
    if not cfg.get("accounts") or len(cfg["accounts"]) < 2:
        sys.exit("配置 accounts 至少需要 2 个（1 发现 + N 监控）")
    for i, a in enumerate(cfg["accounts"]):
        if "account" not in a or "password" not in a:
            sys.exit(f"accounts[{i}] 缺 account/password 字段")
    return cfg


def _abs(p: str) -> str:
    """配置里的相对路径一律按仓库根解析。"""
    q = Path(p)
    return str(q if q.is_absolute() else ROOT / q)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="HSys 采集程序（单入口，--strategy 选择采集内容）")
    ap.add_argument("--strategy", default="",
                    choices=list(STRATEGIES),
                    help="采集策略（默认读配置 strategy 字段，缺省 streak）")
    ap.add_argument("--config", default=str(BOT_DIR / "config.json"),
                    help="配置文件路径（默认 crawl-bot/config.json）")
    ap.add_argument("--check", action="store_true",
                    help="只校验配置并打印摘要，不启动采集")
    ap.add_argument("--list", action="store_true", help="列出可用策略")

    # 先解析已知参数拿策略名，再让策略注册自己的参数，最后完整解析
    args, _ = ap.parse_known_args()
    if args.list:
        for name, (_, desc) in STRATEGIES.items():
            print(f"  {name:12s} {desc}")
        return
    cfg = load_config(Path(args.config))
    name = args.strategy or cfg.get("strategy") or "streak"
    if name not in STRATEGIES:
        sys.exit(f"未知策略: {name}（可用: {list(STRATEGIES)}）")
    module = importlib.import_module(STRATEGIES[name][0])
    module.register_args(ap)
    args = ap.parse_args()

    # 路径类配置统一转绝对路径
    cfg["db_path"] = _abs(cfg.get("db_path", "hsys/crawl-bot/data/streak.db"))
    cfg["log_file"] = _abs(cfg.get("log_file",
                                   "hsys/crawl-bot/logs/crawl-bot.log"))
    if cfg.get("proxies"):
        cfg["proxies"] = _abs(cfg["proxies"])

    if args.check:
        n_mon_total = len(cfg["accounts"]) - 1
        max_acc = int(cfg.get("max_accounts", 0))
        n_mon = min(max_acc, n_mon_total) if max_acc > 0 else n_mon_total
        purge = int(cfg.get("purge_raw_days", 7))
        print("配置校验通过：")
        print(f"  config           = {Path(args.config).resolve()}")
        print(f"  strategy         = {name}（{STRATEGIES[name][1]}）")
        print(f"  entry_url        = {cfg.get('entry_url', '(默认)')}")
        print(f"  accounts         = 1 发现 + {n_mon} 监控")
        print(f"  min_streak       = {cfg.get('min_streak', 4)}")
        print(f"  db_path          = {cfg['db_path']}")
        print(f"  log_file         = {cfg['log_file']}")
        print(f"  proxies          = {cfg.get('proxies') or '(本机直连)'}")
        print(f"  purge_raw_days   = "
              f"{'永不删除' if purge <= 0 else str(purge) + ' 天'}")
        print(f"  push             = {cfg.get('push', {})}")
        return

    Path(cfg["db_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["log_file"]).parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | {message}")
    logger.add(cfg["log_file"], rotation="50 MB", level="DEBUG",
               encoding="utf-8")
    logger.info(f"crawl-bot 启动 | 策略 {name} | 库 {cfg['db_path']} | "
                f"账号 {len(cfg['accounts'])} 个")
    module.run(cfg, args)


if __name__ == "__main__":
    main()

"""P0 代理探针：验证接入代理前的三项未知点（不改核心代码）。

验证项:
  1. token 是否绑 IP —— 直连登录拿 game_token，走代理建 WS 登录，
     成功则说明 token 不绑 IP（账号可灵活调度）；
     被拒（10026/403）则说明绑 IP，登录/刷新/WS 必须同代理（P1 硬性要求）。
  2. websockets proxy 参数实测（HTTP CONNECT 账密认证）。
  3. 每代理 IP 是否享有独立连接额度（同代理连开多条 WS 是否被拒）。

用法:
    uv run python scripts/probe_proxy.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

import websockets

from hdata.auth.session import build_ws_config, get_login
from hdata.client import GameClient
from hdata.protocol.codec import (
    FS_LOGIN, build_login_msg, decode_frame, encode_frame, extract_param)

_cfg = json.loads((Path(__file__).parent.parent
                   / "hsys" / "crawl-bot" / "config.json")
                  .read_text(encoding="utf-8"))
ACCOUNTS = _cfg["accounts"]
ENTRY_URL = _cfg["entry_url"]
GEEPASS = _cfg["geepass_token"]
JFBYM = _cfg["jfbym_token"]

PROXIES_FILE = Path(__file__).parent.parent / "data" / "proxies.json"
CONNECT_INTERVAL_S = 3.0     # 与生产一致的建连间隔


def http_egress_ip(proxy_url: str | None) -> tuple[str, float]:
    """HTTP 出口测试：返回 (出口描述, 耗时秒)。proxy=None 为直连基线。

    国内网络访问海外 echo 服务不稳，按序尝试多个检测点。
    """
    from curl_cffi import requests
    kw = {"timeout": 15}
    if proxy_url:
        kw["proxies"] = {"http": proxy_url, "https": proxy_url}
    endpoints = [
        ("https://myip.ipip.net", "text"),
        ("http://httpbin.org/ip", "json_origin"),
        ("https://api.ipify.org?format=json", "json_ip"),
    ]
    last_err: Exception | None = None
    for url, kind in endpoints:
        t0 = time.time()
        try:
            r = requests.get(url, **kw)
            if kind == "json_ip":
                return r.json().get("ip", "?"), time.time() - t0
            if kind == "json_origin":
                return r.json().get("origin", "?"), time.time() - t0
            return r.text.strip()[:60], time.time() - t0
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("所有出口检测点均失败")


async def login_direct(account: str, password: str) -> dict:
    """直连登录（复用现有缓存会话），返回 session dict。"""
    return await get_login(account, password, entry_url=ENTRY_URL,
                           geepass_token=GEEPASS, jfbym_token=JFBYM)


async def ws_via_proxy(session: dict, proxy_url: str, tag: str) -> bool:
    """走代理建 WS 并发登录帧，返回是否登录成功。"""
    cfg = build_ws_config(session)
    token = session["game_token"]
    pid = session.get("game_player_id", 0)
    t0 = time.time()
    try:
        async with websockets.connect(
                cfg["ws_url"], proxy=proxy_url,
                open_timeout=20, close_timeout=3,
                max_size=50 * 1024 * 1024) as ws:
            await ws.send(encode_frame(
                build_login_msg(token, pid, cfg["device_id"])))
            end = time.time() + 15
            while time.time() < end:
                raw = await ws.recv()
                if isinstance(raw, str):
                    continue
                frame = decode_frame(raw)
                fpid = frame.get("protocolId")
                if fpid == FS_LOGIN:
                    info = extract_param(frame) or {}
                    ok = info.get("status") == 1
                    cost = time.time() - t0
                    if ok:
                        logger.info(f"[{tag}] WS 登录成功 ({cost:.1f}s)")
                    else:
                        logger.error(f"[{tag}] WS 登录被拒: "
                                     f"{info.get('msg')}")
                    return ok
                if fpid == 10026:
                    logger.error(f"[{tag}] 收到 10026：token 失效"
                                 "（说明 token 绑定登录 IP）")
                    return False
            logger.error(f"[{tag}] WS 登录超时")
            return False
    except Exception as e:
        logger.error(f"[{tag}] 连接失败: {type(e).__name__}: {e}")
        return False


async def main():
    proxies = json.loads(PROXIES_FILE.read_text(encoding="utf-8"))
    p1, p2 = proxies[0], proxies[1]

    # ── 第 1 步：HTTP 出口验证 ──
    logger.info("── 第1步：HTTP 出口验证 ──")
    ip0, t0 = http_egress_ip(None)
    logger.info(f"直连基线出口 IP: {ip0} ({t0:.1f}s)")
    for p in (p1, p2):
        try:
            ip, t = http_egress_ip(p["url"])
            logger.info(f"代理[{p['name']}] 出口 IP: {ip} ({t:.1f}s)")
        except Exception as e:
            logger.error(f"代理[{p['name']}] HTTP 出口失败: {e}")
            return

    # ── 第 2 步：强制全量重登，拿保证新鲜的 token ──
    # force_refresh=True 跳过缓存走完整打码登录——缓存会话的站点
    # 凭证可能已死（无法刷新），无法区分"token过期"与"token绑IP"
    logger.info("── 第2步：强制全量重登（打码，保证 token 新鲜）──")
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    accounts = ACCOUNTS[:3]          # lshaoxia1/linbing1/lds001
    sessions = {}
    for a in accounts:
        await client.login(a["account"], a["password"],
                           force_refresh=True)
        sessions[a["account"]] = dict(client._session)
        logger.info(f"{a['account']} 全量登录 OK（token 新鲜）")

    # ── 第 3 步：token 绑 IP 判定（对照实验）──
    # A：新鲜 token × 直连 WS —— 必须成功（证明 token 本身有效）
    # B：同一 token × 代理 WS —— A 成功而 B 被 10026 ⇒ 实锤绑 IP
    logger.info("── 第3步：绑 IP 对照实验（A 直连 / B 代理）──")
    sess0 = sessions["lshaoxia1"]
    ok_a = await ws_via_proxy(sess0, None, "A直连/lshaoxia1")
    if not ok_a:
        logger.error("A 直连即失败：新鲜 token 直连都登不上，"
                     "实验环境异常，终止")
        return
    await asyncio.sleep(CONNECT_INTERVAL_S)
    ok_b = await ws_via_proxy(sess0, p1["url"], "B代理/lshaoxia1")
    if not ok_b:
        logger.warning("结论1：A 直连成功而 B 代理被 10026——"
                       "token 实锤绑定登录 IP。"
                       "生产必须：登录/刷新/WS 全程同一代理（P1 硬性要求）")
        return
    logger.info("结论1：token 不绑 IP（直连登录→代理 WS 成功）")

    # ── 第 4 步：同代理并发额度 + 第二代理 WS ──
    logger.info("── 第4步：并发额度与第二代理 ──")
    await asyncio.sleep(CONNECT_INTERVAL_S)
    ok2 = await ws_via_proxy(sessions["linbing1"], p1["url"],
                             f"{p1['name']}/linbing1")
    await asyncio.sleep(CONNECT_INTERVAL_S)
    ok4 = await ws_via_proxy(sessions["lds001"], p2["url"],
                             f"{p2['name']}/lds001")
    logger.info(f"结论2：代理1 再开第2条 {'成功' if ok2 else '失败'}"
                f"；代理2 WS {'成功' if ok4 else '失败'}")
    logger.info("P0 验证完成")


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")
    asyncio.run(main())

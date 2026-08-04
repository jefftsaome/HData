"""Fingerprint2 x64hash128 (MurmurHash3 x64 128-bit) — Python 复刻。

用于生成乐鱼登录所需的 X-API-FINGER:
    joined = f"{colorDepth}{w},{h}{timezoneOffset}{maxTouch},{touchEvent},{touchStart}{ip}"
    finger = x64hash128(joined, seed=31)

已对拍验证: x64hash128("241920,10804200,false,false219.76.134.210", 31)
           == "99c36b1529f2c9959a5d4aae2e19769f"  (浏览器抓包真实值)
"""
from __future__ import annotations

import json
import random

_MASK = 0xFFFFFFFFFFFFFFFF

# 桌面主流分辨率池（2026 中国大陆桌面统计口径，1920x1080 占绝对多数）。
# 注意：colorDepth 桌面浏览器几乎恒为 24、触屏恒为 0/false/false，
# 这两项随机化反而会与桌面 UA 自相矛盾，故只有分辨率入池。
RESOLUTION_POOL: list[tuple[int, int]] = [
    (1920, 1080),  # 最常见，权重最高（重复占位实现加权）
    (1920, 1080),
    (1920, 1080),
    (1366, 768),
    (1536, 864),   # 1080p 屏 125% 缩放的常见逻辑分辨率
    (1440, 900),
    (1600, 900),
    (2560, 1440),
]

from hdata.paths import cache_dir as _cache_dir

_FINGER_PROFILE_DIR = _cache_dir()

# Chrome 版本池：curl_cffi 0.15 可模拟的最高指纹是 chrome146，池内
# 142/145/146 都有对应的 TLS 指纹目标，UA 大版本必须与 impersonate
# 一致（UA 写 149 而 JA3 是 chrome110 这种自相矛盾会露馅）。
# 权重偏最新版（真实用户集中在最新两三个版本）。
CHROME_VERSION_POOL: list[int] = [146, 146, 146, 145, 142]
DEFAULT_CHROME_VERSION = 146

_UA_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{ver}.0.0.0 Safari/537.36"
)


def ua_for_version(version: int) -> str:
    """Windows 桌面 Chrome UA 串。"""
    return _UA_TEMPLATE.format(ver=version)


def impersonate_for_version(version: int) -> str:
    """curl_cffi impersonate 目标（与 UA 大版本一一对应）。"""
    return f"chrome{version}"


def _default_profile() -> dict:
    return {
        "width": 1920, "height": 1080,
        "color_depth": 24, "max_touch_points": 0,
        "chrome_version": DEFAULT_CHROME_VERSION,
        "ua": ua_for_version(DEFAULT_CHROME_VERSION),
    }


def get_finger_profile(account: str) -> dict:
    """每账号固定一份指纹画像（分辨率/UA 等），首次调用从池中抽取并持久化。

    与浏览器行为对齐：同一台设备的分辨率与浏览器大版本短期不变，
    所以同账号必须恒定；不同账号抽不同组合，避免多账号指纹完全
    一致被聚类。account 为空时返回默认画像（不持久化）。
    """
    if not account:
        return _default_profile()
    path = _FINGER_PROFILE_DIR / f"finger_profile_{account}.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("width") and data.get("height"):
                ver = int(data.get("chrome_version", DEFAULT_CHROME_VERSION))
                return {
                    "width": int(data["width"]),
                    "height": int(data["height"]),
                    "color_depth": int(data.get("color_depth", 24)),
                    "max_touch_points": int(data.get("max_touch_points", 0)),
                    "chrome_version": ver,
                    "ua": data.get("ua") or ua_for_version(ver),
                }
    except (OSError, ValueError):
        pass
    w, h = random.choice(RESOLUTION_POOL)
    ver = random.choice(CHROME_VERSION_POOL)
    profile = {
        "width": w, "height": h,
        "color_depth": 24, "max_touch_points": 0,
        "chrome_version": ver,
        "ua": ua_for_version(ver),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile), encoding="utf-8")
    except OSError:
        pass
    return profile


def get_ua(account: str = "") -> str:
    """该账号的 User-Agent（无账号时给默认最新版）。"""
    return get_finger_profile(account)["ua"] if account else ua_for_version(DEFAULT_CHROME_VERSION)


def get_impersonate(account: str = "") -> str:
    """该账号的 curl_cffi impersonate 目标（与 UA 大版本一致）。"""
    ver = (
        get_finger_profile(account)["chrome_version"] if account
        else DEFAULT_CHROME_VERSION
    )
    return impersonate_for_version(ver)


def _rotl(x: int, r: int) -> int:
    return ((x << r) | (x >> (64 - r))) & _MASK


def _fmix(k: int) -> int:
    k ^= k >> 33
    k = (k * 0xFF51AFD7ED558CCD) & _MASK
    k ^= k >> 33
    k = (k * 0xC4CEB9FE1A85EC53) & _MASK
    k ^= k >> 33
    return k


def x64hash128(data: str, seed: int = 0) -> str:
    """与 fingerprintjs2 x64hash128 完全一致的 MurmurHash3 x64 128。

    输出 32 位小写 hex（h1 在前，h2 在后，各 16 位，注意 fingerprintjs2
    内部按 (h1,h2) 顺序拼接且每个 64 位数转 hex 时高位在前）。
    """
    key = data.encode("utf-8")
    length = len(key)
    nblocks = length // 16

    h1 = seed & _MASK
    h2 = seed & _MASK

    c1 = 0x87C37B91114253D5
    c2 = 0x4CF5AD432745937F

    # ---- body ----
    for i in range(nblocks):
        k1 = int.from_bytes(key[i * 16 : i * 16 + 8], "little")
        k2 = int.from_bytes(key[i * 16 + 8 : (i + 1) * 16], "little")

        k1 = (k1 * c1) & _MASK
        k1 = _rotl(k1, 31)
        k1 = (k1 * c2) & _MASK
        h1 ^= k1

        h1 = _rotl(h1, 27)
        h1 = (h1 + h2) & _MASK
        h1 = (h1 * 5 + 0x52DCE729) & _MASK

        k2 = (k2 * c2) & _MASK
        k2 = _rotl(k2, 33)
        k2 = (k2 * c1) & _MASK
        h2 ^= k2

        h2 = _rotl(h2, 31)
        h2 = (h2 + h1) & _MASK
        h2 = (h2 * 5 + 0x38495AB5) & _MASK

    # ---- tail ----
    tail = key[nblocks * 16 :]
    rem = length & 15

    # 按 fingerprintjs2 标准 switch-fallthrough 实现
    k1 = 0
    k2 = 0
    if rem >= 15:
        k2 ^= tail[14] << 48
    if rem >= 14:
        k2 ^= tail[13] << 40
    if rem >= 13:
        k2 ^= tail[12] << 32
    if rem >= 12:
        k2 ^= tail[11] << 24
    if rem >= 11:
        k2 ^= tail[10] << 16
    if rem >= 10:
        k2 ^= tail[9] << 8
    if rem >= 9:
        k2 ^= tail[8]
        k2 = (k2 * c2) & _MASK
        k2 = _rotl(k2, 33)
        k2 = (k2 * c1) & _MASK
        h2 ^= k2

    if rem >= 8:
        k1 ^= tail[7] << 56
    if rem >= 7:
        k1 ^= tail[6] << 48
    if rem >= 6:
        k1 ^= tail[5] << 40
    if rem >= 5:
        k1 ^= tail[4] << 32
    if rem >= 4:
        k1 ^= tail[3] << 24
    if rem >= 3:
        k1 ^= tail[2] << 16
    if rem >= 2:
        k1 ^= tail[1] << 8
    if rem >= 1:
        k1 ^= tail[0]
        k1 = (k1 * c1) & _MASK
        k1 = _rotl(k1, 31)
        k1 = (k1 * c2) & _MASK
        h1 ^= k1

    # ---- finalization ----
    h1 ^= length
    h2 ^= length

    h1 = (h1 + h2) & _MASK
    h2 = (h2 + h1) & _MASK

    h1 = _fmix(h1)
    h2 = _fmix(h2)

    h1 = (h1 + h2) & _MASK
    h2 = (h2 + h1) & _MASK

    # fingerprintjs2 输出: h1 的 hex 低位补零到 16 + h2 同样处理
    return f"{h1:016x}{h2:016x}"


def leyu_finger(
    ip: str,
    width: int = 1920,
    height: int = 1080,
    color_depth: int = 24,
    timezone_offset: int = 480,
    max_touch_points: int = 0,
) -> str:
    """生成乐鱼 X-API-FINGER。

    Args:
        ip: 客户端出口 IP（从 preInfo 接口或抓包获取，服务端会校验一致性）
        width/height: 屏幕分辨率
        color_depth: 色深
        timezone_offset: JS timezoneOffset（分钟，UTC+8 为 -480 的绝对值描述见 fingerprintjs2;
                         注意 fingerprintjs2 的 timezoneOffset 取自 new Date().getTimezoneOffset()，
                         东八区为 -480；但实测该站点环境为 420(UTC+7)。需与目标环境一致）
        max_touch_points: 触屏点数，桌面为 0
    """
    joined = (
        f"{color_depth}"
        f"{width},{height}"
        f"{timezone_offset}"
        f"{max_touch_points},false,false"
        f"{ip}"
    )
    return x64hash128(joined, 31)


if __name__ == "__main__":
    # 对拍: 浏览器真值
    got = x64hash128("241920,10804200,false,false219.76.134.210", 31)
    want = "99c36b1529f2c9959a5d4aae2e19769f"
    print("joined ->", got)
    print("match:", got == want)
    assert got == want, "x64hash128 与浏览器不一致!"
    print("leyu_finger demo:", leyu_finger("219.76.134.210", timezone_offset=420))

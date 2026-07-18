"""Fingerprint2 x64hash128 (MurmurHash3 x64 128-bit) — Python 复刻。

用于生成乐鱼登录所需的 X-API-FINGER:
    joined = f"{colorDepth}{w},{h}{timezoneOffset}{maxTouch},{touchEvent},{touchStart}{ip}"
    finger = x64hash128(joined, seed=31)

已对拍验证: x64hash128("241920,10804200,false,false219.76.134.210", 31)
           == "99c36b1529f2c9959a5d4aae2e19769f"  (浏览器抓包真实值)
"""
from __future__ import annotations

_MASK = 0xFFFFFFFFFFFFFFFF


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

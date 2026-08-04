"""decrypt_sign_table 等价性测试（Task 4）。

无现成 uuidToBase64 样本（缓存里只有浏览器 profile 二进制，非可用样本），
改为断言新模块与旧入口 TokenManager._decrypt_sign_table 对同一输入行为一致。
"""
from hdata.auth.sign_table import decrypt_sign_table


def test_decrypt_sign_table_matches_token_manager_delegation():
    from hdata.auth.token_manager import TokenManager

    def outcome(fn, arg):
        try:
            return ("ok", fn(arg))
        except Exception as exc:
            return ("raise", type(exc).__name__)

    for sample in ("", "AAAA", "AAAAAAAAAAAAAAAAAAAAAAAAAAA="):
        assert outcome(decrypt_sign_table, sample) == outcome(
            TokenManager._decrypt_sign_table, sample
        )

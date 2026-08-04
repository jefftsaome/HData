"""hdata 环境诊断脚本 — 新 agent/新手接手时一键验证。

检查:解释器/依赖/公共入口/核心子包 import/测试基线/资源资产。

用法:
    uv run python scripts/doctor.py
    或 .venv\\Scripts\\python.exe scripts\\doctor.py
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = "[OK]", "[FAIL]"


def _out(s: str):
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("utf-8", errors="replace").decode("ascii", errors="replace"))


def check(name: str, ok: bool, detail: str = ""):
    mark = PASS if ok else FAIL
    _out(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    ok_all = True

    def report(name: str, ok: bool, detail: str = ""):
        nonlocal ok_all
        ok_all = ok_all and ok
        check(name, ok, detail)

    _out(f"== hdata 环境诊断 ({ROOT}) ==")
    # 1. Python 版本
    report("Python", sys.version_info[:2] == (3, 13),
           f"当前 {sys.version_info.major}.{sys.version_info.minor}，要求 3.13")

    # 2. 依赖
    deps = {
        "curl_cffi": "hdata.auth.http_login",
        "websockets": "hdata.client.transport",
        "cryptography": "hdata.auth.sign_table",
        "Crypto": "hdata.auth.geetest_signer (pycryptodome)",
        "playwright": "hdata.auth.browser_login",
        "aiohttp": "hdata.auth.chrome_manager",
        "python_socks": "hdata.proxy",
        "loguru": "hdata.auth.http_login",
    }
    for dep, used_by in deps.items():
        ok = importlib.util.find_spec(dep) is not None
        report(f"依赖 {dep}", ok, used_by if not ok else "")

    # 3. 公共入口
    try:
        import hdata
        report("import hdata", True)
        for name in ("GameClient", "get_login", "TableSession",
                     "MultiTableSession", "TableMonitor"):
            report(f"hdata.{name}", hasattr(hdata, name))
    except Exception as e:  # noqa: BLE001
        report("import hdata", False, str(e))
        report("全套测试", False, "hdata 不可 import，跳过")
        _out("诊断失败，先修 import。")
        return 1

    # 4. 核心子包
    for mod in ("hdata.auth", "hdata.client", "hdata.protocol",
                "hdata.capture", "hdata.proxy", "hdata.sources"):
        try:
            __import__(mod)
            report(f"import {mod}", True)
        except Exception as e:  # noqa: BLE001
            report(f"import {mod}", False, str(e))

    # 5. 资源资产
    for asset in (
        "hdata/protocol/schema_data.json",
        "hdata/capture/js/dynamic_extract.js",
        "hdata/auth/wasm_api_sign_bg.wasm",
    ):
        report(f"资产 {asset}", (ROOT / asset).exists())

    # 6. 测试基线(快速:仅收集 + 跑关键测试文件)
    report("测试(收集)", _run_pytest(["tests/test_schemacodec.py",
                                       "tests/test_characterization_client.py",
                                       "tests/test_import_smoke.py"]))

    _out("")
    _out("== 诊断完成 ==")
    if ok_all:
        _out("环境健康，可以开始。入门见 AGENTS.md / docs/ARCHITECTURE.md")
        return 0
    _out("存在失败项，按 [FAIL] 详情排查。")
    return 1


def _run_pytest(paths: list[str]) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *paths],
            cwd=str(ROOT), capture_output=True, timeout=180)
        return r.returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())

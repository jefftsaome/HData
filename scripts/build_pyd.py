"""HData 打包脚本 — 将 hdata 包编译为 .pyd(Windows)/.so(Linux)。

用法:
    .venv/Scripts/python.exe scripts/build_pyd.py

产物:
    dist_pyd/hdata/**.pyd   — 编译后的二进制包（隐藏源码实现）
    dist_pyd/*.pyd 与包结构保持一致，外部直接 `from hdata.client import GameClient`

环境要求（缺一不可）:
    1. Python 3.13.5（与 pyproject.toml requires-python 一致；
       .pyd 与解释器版本强绑定，目标机器必须是同一大版本）
    2. C 编译器:
       - Windows: MSVC "Visual Studio Build Tools"（勾选 C++ 生成工具）
       - Linux:   gcc
       - macOS:   clang (Xcode Command Line Tools)
    3. pip install cython setuptools wheel

注意:
    - 本脚本只编译 hdata 包本体；第三方依赖（aiohttp/websockets/
      curl-cffi/pycryptodome 等）仍需在目标环境正常 pip 安装。
    - 编译后 .py 源码不会出现在产物目录，实现细节（协议/加密）不可见。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist_pyd"
PKG = ROOT / "hdata"


def collect_modules() -> list[str]:
    """收集 hdata 包下所有 .py 模块（转点分模块名）。"""
    mods = []
    for py in sorted(PKG.rglob("*.py")):
        rel = py.relative_to(ROOT).with_suffix("")
        mods.append(".".join(rel.parts))
    return mods


def main() -> int:
    try:
        from Cython.Build import cythonize
        from setuptools import Extension, setup
    except ImportError:
        print("缺少依赖: pip install cython setuptools wheel")
        return 1

    modules = collect_modules()
    print(f"将编译 {len(modules)} 个模块:")
    for m in modules:
        print(f"  {m}")

    if OUT.exists():
        shutil.rmtree(OUT)

    exts = [
        Extension(m, [str(ROOT / (m.replace(".", "/") + ".py"))])
        for m in modules
    ]

    sys.argv = [sys.argv[0], "build_ext", f"--build-lib={OUT}"]
    import os
    compiler = os.environ.get("HDATA_COMPILER", "").strip()
    script_args = ["build_ext", f"--build-lib={OUT}"]
    if compiler:
        script_args.append(f"--compiler={compiler}")
    setup(
        name="hdata",
        ext_modules=cythonize(
            exts,
            language_level="3",
            compiler_directives={
                "boundscheck": False,
                "wraparound": False,
                "cdivision": True,
                "embedsignature": True,   # 保留函数签名，方便 help()
            },
        ),
        script_args=script_args,
    )

    print(f"\n产物目录: {OUT}")
    print("验证: 将 dist_pyd 加入 sys.path 后")
    print("  from hdata.client import GameClient")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

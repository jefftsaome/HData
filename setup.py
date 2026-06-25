import os
import sys

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.install_lib import install_lib as _install_lib

# 仅在 CYTHON_BUILD=1 时启用 Cython 编译（生产交付使用）
# 开发时直接用 .py 源码，避免 .so 缓存干扰
cython_build = os.environ.get("CYTHON_BUILD") == "1"

if cython_build:
    from Cython.Build import cythonize

    class build_py(_build_py):
        """Skip .py files for hdt packages, keep only .so compiled extensions."""
        def find_package_modules(self, package, package_dir):
            modules = super().find_package_modules(package, package_dir)
            if package.startswith("hdt"):
                return [(pkg, mod, path) for pkg, mod, path in modules if not path.endswith(".py")]
            return modules

    class install_lib(_install_lib):
        """Remove intermediate .c files from the install directory."""
        def install(self):
            outfiles = super().install()
            for dirpath, _, filenames in os.walk(self.install_dir):
                for f in filenames:
                    if f.endswith(".c") and os.path.join(dirpath, f).startswith(os.path.join(self.install_dir, "hdt")):
                        filepath = os.path.join(dirpath, f)
                        os.remove(filepath)
                        if filepath in outfiles:
                            outfiles.remove(filepath)
            return outfiles

    setup_kwargs = {
        "cmdclass": {"build_py": build_py, "install_lib": install_lib},
        "ext_modules": cythonize(
            ["hdt/**/*.py"],
            compiler_directives={"language_level": "3", "binding": False},
        ),
    }
else:
    setup_kwargs = {}


setup(**setup_kwargs)

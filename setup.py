import os

from setuptools import setup
from Cython.Build import cythonize
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.install_lib import install_lib as _install_lib


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
        install_dir = self.install_dir
        for dirpath, _, filenames in os.walk(install_dir):
            for f in filenames:
                if f.endswith(".c") and dirpath.startswith(os.path.join(install_dir, "hdt")):
                    filepath = os.path.join(dirpath, f)
                    os.remove(filepath)
                    if filepath in outfiles:
                        outfiles.remove(filepath)
        return outfiles


setup(
    cmdclass={"build_py": build_py, "install_lib": install_lib},
    ext_modules=cythonize(
        ["hdt/**/*.py"],
        compiler_directives={
            "language_level": "3",
            "binding": False,
        },
    ),
)

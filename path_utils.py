"""路径工具 — 兼容开发环境和 PyInstaller 打包后的路径解析。

PyInstaller 打包后，sys._MEIPASS 指向解压资源的临时目录；
用户数据（projects、uploads、exports）存入系统用户数据目录。
"""

import os
import sys


APP_NAME = "PBC文件核对工具"


def get_bundle_path(relative_path=""):
    """获取只读资源的绝对路径（兼容 PyInstaller 打包和开发环境）。

    PyInstaller 打包后资源被解压到 sys._MEIPASS 临时目录，
    开发环境中则以脚本所在目录为基准。
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def get_data_dir(*subdirs):
    """获取用户数据目录的绝对路径，不存在则自动创建。

    存入系统推荐的用户数据位置，不受 PyInstaller 临时目录影响：
    - Windows: %APPDATA%/PBC审计工具/
    - macOS:   ~/Library/Application Support/PBC审计工具/
    - Linux:   ~/.local/share/PBC审计工具/
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif os.sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share")

    data_dir = os.path.join(base, APP_NAME, *subdirs)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

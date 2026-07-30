"""Hermes CLI 兼容入口。

业务逻辑位于 ``hermes`` 包中；保留本文件以兼容现有启动命令。
"""

from hermes.interfaces.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    main()

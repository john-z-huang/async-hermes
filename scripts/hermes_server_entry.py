"""PyInstaller 冻结入口：保留 ``hermes.interfaces`` 的包导入上下文。"""

from hermes.interfaces.grpc_server import main


if __name__ == "__main__":
    main()

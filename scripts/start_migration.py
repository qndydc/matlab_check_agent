"""
Description: 一键启动迁移前后端，与语义项目共享启动和退出逻辑。
References: start_mvp.start_app。
Referenced By: 迁移子工程本地开发者。
"""

from start_mvp import start_app


def main() -> None:
    start_app("migration", 8001, 5174)


if __name__ == "__main__":
    main()

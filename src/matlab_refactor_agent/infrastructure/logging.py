"""
Description: 初始化应用标准日志格式和级别。
References: Python logging。
Referenced By: interfaces.cli.main。
"""

import logging


def configure_logging(level: str = "INFO") -> None:
    """作用：初始化标准日志；输入：日志级别；输出：无；数据流：配置值 -> logging 全局设置。"""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # 默认保留阶段与错误，HTTP 轮询和 SDK 请求细节只在 DEBUG 时输出。
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(
            logging.DEBUG if level.upper() == "DEBUG" else logging.WARNING
        )

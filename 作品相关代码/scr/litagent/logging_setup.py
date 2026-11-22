"""应用日志初始化。

日志只记录运行状态、耗时和异常类型，不记录完整查询文本、密钥或带会话
参数的来源链接。日志按大小轮转，避免长期运行后无限增长。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from litagent.config import Settings

_HANDLER_MARKER="_litagent_file_handler"
_READY_PATHS:set[Path]=set()


def setup_logging(
    settings:Settings,
    *,
    component:str="lit-agent",
    level:int=logging.INFO,
) -> Optional[Path]:
    """为当前进程配置轮转文件日志，重复调用不会叠加处理器。"""
    try:
        settings.logs_dir.mkdir(parents=True,exist_ok=True)
        path=settings.logs_dir/"litagent.log"
    except OSError:
        return None

    logger=logging.getLogger("litagent")
    logger.setLevel(level)
    if not any(
        getattr(handler,_HANDLER_MARKER,False)
        for handler in logger.handlers
    ):
        handler=RotatingFileHandler(
            path,
            maxBytes=2*1024*1024,
            backupCount=5,
            encoding="utf-8",
        )
        setattr(handler,_HANDLER_MARKER,True)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        ))
        logger.addHandler(handler)
    if path not in _READY_PATHS:
        _READY_PATHS.add(path)
        logger.info("logging ready component=%s",component)
    return path


__all__=["setup_logging"]

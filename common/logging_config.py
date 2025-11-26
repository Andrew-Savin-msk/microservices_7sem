"""
Общий модуль для настройки структурированного логирования.
Все микросервисы используют этот модуль для единообразного логирования.
"""
import json
import logging
import sys
from datetime import datetime
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Форматтер для структурированного JSON-логирования."""
    
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name
    
    def format(self, record: logging.LogRecord) -> str:
        """Форматирует лог-запись в JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": self.service_name,
            "message": record.getMessage(),
        }
        
        # Добавляем дополнительные поля, если они есть
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        if hasattr(record, "method"):
            log_data["method"] = record.method
        if hasattr(record, "path"):
            log_data["path"] = record.path
        if hasattr(record, "status_code"):
            log_data["status_code"] = record.status_code
        
        # Добавляем exception info, если есть
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


def setup_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    """
    Настраивает логгер для микросервиса.
    
    Args:
        service_name: Название сервиса (например, "user-service")
        level: Уровень логирования (INFO, DEBUG, WARNING, ERROR)
    
    Returns:
        Настроенный логгер
    """
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Удаляем существующие handlers, чтобы избежать дублирования
    logger.handlers.clear()
    
    # Создаем handler для stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service_name))
    logger.addHandler(handler)
    
    # Предотвращаем распространение логов в root logger
    logger.propagate = False
    
    return logger


"""
Общий middleware для логирования и метрик.
"""
import time
import uuid
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import logging

http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code', 'service']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint', 'status_code', 'service'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

http_errors_total = Counter(
    'http_errors_total',
    'Total HTTP errors (5xx)',
    ['method', 'endpoint', 'service']
)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware для логирования HTTP-запросов."""
    
    def __init__(self, app, service_name: str, logger: logging.Logger):
        super().__init__(app)
        self.service_name = service_name
        self.logger = logger
    
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        
        request.state.request_id = request_id
        
        start_time = time.time()
        
        self.logger.info(
            f"Request started: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            }
        )
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            self.logger.info(
                f"Request completed: {request.method} {request.url.path} - {response.status_code}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                }
            )
            
            endpoint = request.url.path
            http_requests_total.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code,
                service=self.service_name
            ).inc()
            
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=response.status_code,
                service=self.service_name
            ).observe(process_time)
            
            if response.status_code >= 500:
                http_errors_total.labels(
                    method=request.method,
                    endpoint=endpoint,
                    service=self.service_name
                ).inc()
            
            return response
            
        except Exception as e:
            process_time = time.time() - start_time
            self.logger.error(
                f"Request failed: {request.method} {request.url.path}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "exception": str(e),
                },
                exc_info=True
            )
            
            http_errors_total.labels(
                method=request.method,
                endpoint=request.url.path,
                service=self.service_name
            ).inc()
            
            raise


def setup_metrics_endpoint(app, service_name: str):
    """Добавляет эндпоинт /metrics для экспорта метрик Prometheus."""
    
    @app.get("/metrics")
    async def metrics():
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST
        )


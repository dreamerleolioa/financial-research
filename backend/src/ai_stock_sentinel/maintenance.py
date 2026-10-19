"""Admission gate for draining application traffic during a database cutover."""
import os

from starlette.responses import JSONResponse


class MaintenanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Unknown configured values fail closed. Unconfigured existing installs
        # retain normal behavior; this flag is managed by the cutover operator.
        enabled = os.getenv('APP_MAINTENANCE_MODE', 'false').lower() != 'false'
        health = scope.get('path') == '/health' and scope.get('method') == 'GET'
        if scope['type'] == 'http' and enabled and not health and scope.get('method') != 'OPTIONS':
            response = JSONResponse({'detail': 'Service temporarily unavailable for maintenance'},
                                    status_code=503, headers={'Retry-After': '60'})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

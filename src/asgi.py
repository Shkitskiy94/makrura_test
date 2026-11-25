from granian import Granian
from granian.constants import Interfaces, Loops
from python_common.logger import build_logging_config

from settings import get_settings


def get_server() -> Granian:  # type: ignore[valid-type]
    settings = get_settings()
    return Granian(
        "app.main:app",
        reload=settings.DEBUG,
        port=settings.SERVER_PORT,
        address=settings.SERVER_BIND,
        loop=Loops.uvloop,
        interface=Interfaces.ASGI,
        workers=settings.SERVER_WORKERS,
        log_dictconfig=build_logging_config(
            level=settings.LOG_LEVEL,
            pretty=settings.LOG_PRETTY,
        ),
        log_access=False,
    )


server = get_server()

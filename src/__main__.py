import sys
from os.path import abspath, join

import inject
import uvicorn

sys.path.insert(0, abspath(join(__file__, "../")))
sys.path.insert(0, abspath(join(__file__, "../", "../")))

# Adjust the paths
# Run the ASGI server
from app.dependency import configure_dependency
from common.base.settings import CoreSettings

if not inject.is_configured():
    inject.configure(configure_dependency)

settings = inject.instance(CoreSettings)

if __name__ == "__main__":
    uvicorn.run(
        "app.bootstrap:api",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.can_reload,
    )

import typing

import inject

from common.base.settings import CoreSettings
from settings import Settings

from .dependency import configure_dependency

# Configure the Dependencies for the Application
if not inject.is_configured():
    inject.configure(configure_dependency)


def init_app():
    # Loading apps
    import api  # noqa
    from common.base.bootstrap import create_app

    api_ = create_app(typing.cast(Settings, inject.instance(CoreSettings)))
    return api_


# Create the FAST API app
api = init_app()

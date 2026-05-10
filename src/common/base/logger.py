import logging

import inject

from common.base.middlewares import get_request_id
from common.base.settings import CoreSettings


class AppFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True


@inject.autoparams("conf")
def setup_logging(conf: CoreSettings):
    logger = logging.getLogger()
    syslog = logging.FileHandler(conf.log_file, delay=True)
    syslog.addFilter(AppFilter())

    formatter = logging.Formatter(
        "[%(asctime)s] [%(process)s] [%(levelname)s] " "[%(request_id)s] [%(name)s] " "%(message)s"
    )

    syslog.setFormatter(formatter)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(syslog)

    if conf.is_local:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

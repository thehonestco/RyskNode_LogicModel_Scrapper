import __about__
from common.base.settings import CoreSettings


class Settings(CoreSettings):
    """ """

    app_title: str = __about__.__NAME__
    app_version: str = __about__.__VERSION__
    api_version: str = __about__.__API_VERSION__
    app_description: str = __about__.__DESCRIPTION__

    data_gov_api_key: str | None = None
    data_gov_resource_id: str = "4dbe5667-7b6b-41d7-82af-211562424d9a"
    data_gov_base_url: str = "https://api.data.gov.in/resource"
    data_gov_rate_limit_cooldown: int = 120
    data_gov_api_limit: int = 10

    # ── Celery / Redis ───────────────────────────────────────────────
    enable_api_server: bool = True
    enable_celery_worker: bool = True
    enable_celery_events: bool = True
    redis_host: str = "localhost"
    redis_port: int = 6379
    celery_broker_url: str = "redis://:default@localhost:6379/0"
    celery_app_conf: str = "asyncworker.settings.celeryconfig"
    celery_queue_default: str = "default"
    celery_queue_buyer_risk: str = "buyer_risk"
    celery_queue_credit_limit: str = "credit_limit"
    celery_queue_vdd: str = "vendor_due_diligence"
    enable_queue_default: bool = True
    enable_queue_buyer_risk: bool = True
    enable_queue_credit_limit: bool = True
    enable_queue_vdd: bool = True
    celery_concurrency_default: int = 1
    celery_concurrency_buyer_risk: int = 4
    celery_concurrency_credit_limit: int = 2
    celery_concurrency_vdd: int = 2
    celery_task_time_limit: int = 600
    celery_task_soft_time_limit: int = 540
    celery_event_app_name: str = "rysknode"

    # ── Flower UI ────────────────────────────────────────────────────
    enable_celery_flower: bool = True
    celery_flower_port: int = 5555
    celery_flower_user: str = "admin"
    celery_flower_password: str = "flowerpass"


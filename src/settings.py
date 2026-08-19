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
    enable_celery_worker: bool = True
    redis_host: str = "localhost"
    redis_port: int = 6379
    celery_broker_url: str = "redis://:default@localhost:6379/0"
    celery_result_backend: str | None = None  # defaults to broker_url
    celery_app_conf: str = "asyncworker.settings.celeryconfig"
    celery_worker_concurrency: int = 4
    celery_worker_queues: str = "default,buyer_risk,credit_limit,sync"
    celery_task_time_limit: int = 600
    celery_task_soft_time_limit: int = 540
    celery_event_app_name: str = "rysknode"

    # ── Flower UI ────────────────────────────────────────────────────
    enable_celery_flower: bool = True
    celery_flower_port: int = 5555
    celery_flower_user: str = "admin"
    celery_flower_password: str = "flowerpass"

    # ── Celery Beat (Periodic Scheduler) ─────────────────────────────
    enable_celery_beat: bool = False
    enable_celery_beat_sync: bool = False
    celery_beat_sync_cron_hour: str = "2"
    celery_beat_sync_cron_minute: str = "0"
    celery_beat_sync_statecode: str | None = None


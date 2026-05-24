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

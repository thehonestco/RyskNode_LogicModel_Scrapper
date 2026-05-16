import abc
from typing import Any, Dict


class BaseScraper(abc.ABC):
    @abc.abstractmethod
    async def scrape(self, query: str) -> Dict[str, Any]:
        """
        Scrape data for a given query (CIN or Company Name).
        """
        raise NotImplementedError

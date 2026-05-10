"""Provides the backend storage for the application
"""
import decimal
import json
import typing
from datetime import date, datetime
from logging import getLogger

import redis

from common.adapter.base import BaseBackend

logger = getLogger(__name__)


class DecimalJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            # Serialize the Decimal
            return f"{o:.10f}"
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        # Let the base class default method raise the TypeError
        return super(DecimalJSONEncoder, self).default(o)


class RedisBackend(BaseBackend):
    """Implements Backend as redis serer"""

    def __init__(self, **con_settings):
        super().__init__()
        try:
            self.conn = redis.StrictRedis(**con_settings)
        except redis.ConnectionError as ce:
            logger.fatal(f"Unable to connect with Redis : {ce}")

    def set_str(self, key, data, **kwargs) -> bool:
        try:
            data = self._serialize(data)
            self.conn.set(key, data, **kwargs)
        except redis.exceptions.DataError as de:
            logger.fatal("Error while retrieving data for {} to redis: {}" % key, de)
            return False
        return True

    def get_str(self, key, **kwargs) -> str:
        tmp = None
        try:
            tmp = self.conn.get(key)
        except redis.exceptions.DataError as de:
            logger.fatal(f"Error while retrieving data for {key} to redis: {str(de)}")
        logger.debug(f"For key {key} value {tmp}")
        return tmp.decode("utf-8") if tmp else ""

    def set_dict(self, key, data, **kwargs) -> bool:
        """Sets the dictionary to the redis server
        :param: key: unique string to identify dictionary :type: str
        :param: data: dictionary object to store :type: dict
        """

        try:
            data = self._serialize(data)
            self.conn.set(key, data, **kwargs)
        except redis.exceptions.DataError as de:
            logger.fatal("Error while retrieving data for {} to redis: {}" % key, de)
            return False
        return True

    def get_dict(self, key: str) -> typing.Union[None, dict]:
        """Retrieves dictionary with given key

        :param: key: unique string to identify dictionary :class: str
        """
        tmp = {}
        try:
            tmp = self.conn.get(key)
            try:
                obj_tmp = self._deserialize(tmp or "{}")
            except json.decoder.JSONDecodeError:
                logger.fatal(f"Error in json decoding for {key} from cache")
            except AttributeError as ae:
                logger.fatal(f"For key {key} AttributeError: {str(ae)}")
            else:
                tmp = obj_tmp
        except redis.exceptions.DataError as de:
            logger.fatal(f"Error while retrieving data for {key} to redis: {str(de)}")
        logger.debug(f"For key {key} value {tmp}")
        return tmp

    def set_list(self, key: str, data: list, **kwargs) -> bool:
        """

        :param key:
        :param data:
        :param kwargs:
        :return:
        """
        try:
            data = self._serialize(data)
            self.conn.set(key, data, **kwargs)
        except redis.exceptions.DataError as de:
            logger.fatal("Error while retrieving data for {} to redis: {}" % key, de)
            return False
        return True

    def get_list(self, key: str) -> typing.Union[None, list]:
        """

        :param key:
        :return:
        """
        tmp = None
        try:
            tmp = self.conn.get(key)
            try:
                obj_tmp = self._deserialize(tmp or "[]")
            except json.decoder.JSONDecodeError:
                logger.fatal(f"Error in json decoding for {key} from cache")
            except AttributeError as ae:
                logger.fatal(f"For key {key} AttributeError: {str(ae)}")
            else:
                tmp = obj_tmp
        except redis.exceptions.DataError as de:
            logger.fatal(f"Error while retrieving data for {key} to redis: {str(de)}")
        logger.debug(f"For key {key} value {tmp}")
        return tmp

    @staticmethod
    def _serialize(data):
        """

        :param data:
        :return:
        """
        if type(data) is str:
            return data
        return json.dumps(data, cls=DecimalJSONEncoder)

    @staticmethod
    def _deserialize(data):
        """

        :param data:
        :return:
        """
        if type(data) in [bytes, bytearray]:
            data = data.decode("utf-8")  # Decode from Binary to utf-8
        return json.loads(data, parse_float=decimal.Decimal)  # Decode json from string

    def find_keys(self, pattern):
        """

        :param pattern:
        :return:
        """
        for k in self.conn.keys(pattern):
            yield k

    def scan(self, match_: str, **kwargs) -> str:
        """
        iterates over the key pattern
        :param match_:
        :param kwargs:
        :return:
        """
        for val in self.conn.scan_iter(match_, **kwargs):
            yield val

    def delete(self, *keys):
        self.conn.delete(*keys)

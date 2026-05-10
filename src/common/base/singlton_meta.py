from typing import Any, Dict


class SingletonMeta(type):
    """
    Helps to create the singleton instances
    """

    _instances: Dict[Any, Any] = {}

    def __call__(cls, *args, **kwargs):  # type: ignore
        """

        :param args:
        :param kwargs:
        :return:
        """
        if cls not in cls._instances:
            cls._instances[cls] = super(SingletonMeta, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

import requests
import json
import logging

logger = logging.getLogger(__name__)


class InventoryClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def fetch_current_stock(self, product_id: str) -> int:
        """
        跨服务查询库存容量
        """
        resp = requests.get(f"{self.base_url}/api/inventory/{product_id}")

        data = resp.json()

        return data["data"]["stock"]

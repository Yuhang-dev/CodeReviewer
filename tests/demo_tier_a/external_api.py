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
        # [Bug 1: 缺少异常重试和 Timeout] 网络很可能出现抖动，这里如果没有设置 Timeout 也没有重试机制，线程会一直挂起
        resp = requests.get(f"{self.base_url}/api/inventory/{product_id}")
        
        # [Bug 2: 缺乏边界和状态码处理] 没有判断 resp.status_code == 200，它如果是 500，下方的 json() 解析直接崩掉
        data = resp.json()
        
        # [Bug 3: 字典防御性访问] 没有做 data.get("data", {}).get("stock", 0) 这种安全取值，如果返回包体不对直接抛 KeyError 宕机业务。
        return data["data"]["stock"]

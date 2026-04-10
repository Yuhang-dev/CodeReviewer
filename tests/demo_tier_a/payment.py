import logging

logger = logging.getLogger(__name__)

class PaymentSystem:
    def __init__(self, db_session):
        self.db = db_session

    def process_payment_callback(self, user_id: int, order_id: str, amount: float):
        """
        处理支付网关异步回调的方法
        """
        logger.info(f"Received payment callback for Order {order_id}")
        
        # [Bug 1: 幂等性缺失] 没有检查这个 order_id 是否已经被支付成功过，直接给用户加钱/发货。如果支付系统重发回调，会导致重复发货！
        
        # [Bug 2: 空指针隐患] 直接调用 .get_user 会不会返回 None？后面直接 user.balance 会抛出异常
        user = self.db.get_user(user_id)
        user.balance += amount
        
        # 记录流水并结单
        self.db.update_order_status(order_id, "PAID")
        self.db.commit()
        
        return {"status": "success"}

    def refund_order(self, order_id: str):
        """
        处理退款申请
        """
        order = self.db.get_order(order_id)
        
        # [Bug 3: 边界条件遗漏] 完全没有判断这笔订单的当前状态（例如订单本来就是 REFUNDED 或者 CANCELLED 就不该退款）
        
        user = self.db.get_user(order.user_id)
        # [Bug 4: 越界操作风险] 直接减钱，不判断用户的余额够不够（或者是退到负数）
        user.balance -= order.amount 
        
        self.db.commit()
        return True

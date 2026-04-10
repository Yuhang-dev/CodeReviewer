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


        user = self.db.get_user(user_id)
        user.balance += amount

        self.db.update_order_status(order_id, "PAID")
        self.db.commit()

        return {"status": "success"}

    def refund_order(self, order_id: str):
        """
        处理退款申请
        """
        order = self.db.get_order(order_id)


        user = self.db.get_user(order.user_id)
        user.balance -= order.amount

        self.db.commit()
        return True

import sqlite3

def process_payment(user_id: int, amount: float):
    """
    处理用户的支付请求。
    这个函数涉及核心资金流转，Planner 应当将其评估为 Tier-S 或 Tier-A。
    """
    # 模拟一个没有使用事务，且直接拼装 SQL 的极其危险的操作
    # 这应该会触发高风险定级，并被 Reviewer/Critic 抓住
    conn = sqlite3.connect("ecommerce.db")
    cursor = conn.cursor()
    
    # 扣除用户余额 (存在明显的 SQL 注入漏洞和并发安全问题)
    update_sql = f"UPDATE user_wallets SET balance = balance - {amount} WHERE user_id = {user_id}"
    cursor.execute(update_sql)
    
    conn.commit()
    conn.close()
    return True

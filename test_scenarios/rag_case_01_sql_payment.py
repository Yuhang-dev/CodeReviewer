import sqlite3


def debit_wallet(user_id: str, amount: str) -> bool:
    """
    RAG target:
    - Should match sql-injection / payment / wallet rules.
    - Planner should infer a high tier because this mutates a wallet balance.
    - Reviewer should cite the parameterized-query rule if retrieval works.
    """
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    sql = f"UPDATE user_wallets SET balance = balance - {amount} WHERE user_id = {user_id}"
    cursor.execute(sql)
    conn.commit()
    conn.close()
    return True


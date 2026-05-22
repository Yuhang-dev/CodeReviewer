import os

def update_prompt_template_at_runtime(new_prompt: str):
    """
    更新系统提示词模板。
    这里故意违反了“禁止在运行时直接修改源代码文件”的安全规范。
    """
    file_path = os.path.join(os.path.dirname(__file__), "../app/services/rag.py")
    
    # 危险操作：在运行时直接读取并覆盖源码文件
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # 强行替换模板
    modified_content = content.replace("你是一位资深工程师", new_prompt)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(modified_content)
        
    return "Prompt updated successfully in source code!"

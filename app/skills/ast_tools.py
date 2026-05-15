import os
import ast
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

class FunctionCallVisitor(ast.NodeVisitor):
    def __init__(self, target_func_name: str):
        self.target_func_name = target_func_name
        self.calls = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id == self.target_func_name:
                self.calls.append(node.lineno)
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr == self.target_func_name:
                self.calls.append(node.lineno)
        self.generic_visit(node)


@tool
def find_python_references(repo_path: str, function_name: str) -> str:
    """
    遍历指定仓库路径下的所有 Python 文件，查找特定函数被调用的位置。
    当且仅当检测到可能引起跨文件破坏性变更（如修改了核心函数参数）时调用。
    
    参数:
    - repo_path: 仓库的绝对路径（由浅克隆产生）
    - function_name: 要查找的被调用函数名
    
    返回:
    - 包含所有调用方文件路径和对应行号的文本报告。如果找不到则提示未查到。
    """
    logger.info(f"[AST Tool] Searching references for '{function_name}' in {repo_path}")
    
    if not os.path.exists(repo_path):
        return f"错误: 仓库路径 {repo_path} 不存在。"
        
    results = []
    
    # Walk through the repository
    for root, _, files in os.walk(repo_path):
        # Skip hidden directories like .git
        if "/." in root.replace("\\", "/") or "\\." in root:
            continue
            
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        source = f.read()
                    
                    tree = ast.parse(source, filename=file_path)
                    visitor = FunctionCallVisitor(function_name)
                    visitor.visit(tree)
                    
                    if visitor.calls:
                        rel_path = os.path.relpath(file_path, repo_path)
                        lines_str = ", ".join(map(str, sorted(set(visitor.calls))))
                        results.append(f"文件: `{rel_path}`, 行号: [{lines_str}]")
                except Exception as e:
                    # Ignore syntax errors in individual files (e.g., python2 code)
                    continue
                    
    if not results:
        return f"未在仓库的 .py 文件中找到对 `{function_name}` 的直接调用。"
        
    return "AST 查找结果:\n" + "\n".join(results)


@tool
def read_code_snippet(repo_path: str, relative_file_path: str, start_line: int, end_line: int) -> str:
    """
    读取指定文件的代码片段。通常在用 find_python_references 找到调用行号后，
    使用此工具读取调用处的上下文（建议读取目标行前后各 5 行），以便判断兼容性。
    
    参数:
    - repo_path: 仓库的绝对路径
    - relative_file_path: 文件的相对路径（如 app/main.py）
    - start_line: 起始行号（1-indexed）
    - end_line: 结束行号（包含）
    """
    full_path = os.path.join(repo_path, relative_file_path)
    if not os.path.exists(full_path):
        return f"错误: 文件 {relative_file_path} 不存在。"
        
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Adjust 1-indexed to 0-indexed bounds
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        
        snippet = lines[start_idx:end_idx]
        
        # Add line numbers to the snippet
        formatted_snippet = []
        for i, line in enumerate(snippet, start=start_idx + 1):
            formatted_snippet.append(f"{i}: {line.rstrip()}")
            
        return f"--- {relative_file_path} (Lines {start_line}-{end_line}) ---\n" + "\n".join(formatted_snippet)
    except Exception as e:
        return f"读取文件失败: {str(e)}"

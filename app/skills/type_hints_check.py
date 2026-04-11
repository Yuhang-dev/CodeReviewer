import re
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

@tool
def type_hints_check(code_diff: str) -> str:
    """
    检查 Python 代码变更中是否存在缺少 Type Hints 的函数定义。
    当需要对 Python 代码进行规范审查时调用此工具。
    返回所有缺少类型注解的函数名列表（str），若全部合规则返回空字符串。
    """
    logger.info("[Skill] type_hints_check triggered.")
    
    # Match function definitions that are missing return type or param types
    # Pattern: def func_name(args) - without -> type at end
    func_pattern = re.compile(
        r'^\+.*def\s+(\w+)\s*\(([^)]*)\)\s*(?!->)',
        re.MULTILINE
    )
    
    issues = []
    for match in func_pattern.finditer(code_diff):
        func_name = match.group(1)
        params = match.group(2).strip()
        
        # Skip dunder methods and self/cls-only functions
        if func_name.startswith('__') and func_name.endswith('__'):
            continue
        
        # Check if params have type hints (simple heuristic: look for ":" in params)
        params_list = [p.strip() for p in params.split(',') if p.strip() not in ('self', 'cls', '')]
        missing_hints = [p for p in params_list if ':' not in p and '*' not in p]
        
        if missing_hints:
            issues.append(f"函数 `{func_name}` 参数缺少 Type Hints: {', '.join(missing_hints)}")
    
    if not issues:
        return ""
    
    return "\n".join(issues)

import re
import logging
from langchain_core.tools import tool
from app.skills.diff_utils import parse_diff_line_map

logger = logging.getLogger(__name__)

@tool
def type_hints_check(code_diff: str) -> str:
    """
    检查 Python 代码变更中是否存在缺少 Type Hints 的函数定义。
    当需要对 Python 代码进行规范审查时调用此工具。
    返回所有缺少类型注解的函数名列表（str），若全部合规则返回空字符串。
    """
    logger.info("[Skill] type_hints_check triggered.")
    
    line_map = parse_diff_line_map(code_diff)
    issues = []

    for diff_idx, raw_line in enumerate(code_diff.splitlines(), start=1):
        if not raw_line.startswith('+') or raw_line.startswith('+++'):
            continue
        stripped = raw_line[1:].strip()

        match = re.match(r'def\s+(\w+)\s*\(([^)]*)\)', stripped)
        if not match:
            continue
        
        func_name = match.group(1)
        params = match.group(2).strip()
        
        # Skip dunder methods
        if func_name.startswith('__') and func_name.endswith('__'):
            continue
        
        params_list = [p.strip() for p in params.split(',') if p.strip() not in ('self', 'cls', '')]
        missing_hints = [p for p in params_list if ':' not in p and '*' not in p]
        
        if missing_hints:
            actual_line = line_map.get(diff_idx, diff_idx)
            issues.append(f"第 {actual_line} 行函数 `{func_name}` 参数缺少 Type Hints: {', '.join(missing_hints)}")
    
    if not issues:
        return ""
    
    return "\n".join(issues)

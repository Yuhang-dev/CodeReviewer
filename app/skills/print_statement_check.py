import re
import logging
from langchain_core.tools import tool
from app.skills.diff_utils import parse_diff_line_map

logger = logging.getLogger(__name__)

@tool
def print_statement_check(code_diff: str) -> str:
    """
    检查代码变更中是否存在不该出现在生产代码中的 print() 调用。
    当需要对代码做规范审查时调用此工具。
    返回包含 print 语句的行号与内容，若无则返回空字符串。
    """
    logger.info("[Skill] print_statement_check triggered.")
    
    line_map = parse_diff_line_map(code_diff)
    issues = []

    for diff_idx, raw_line in enumerate(code_diff.splitlines(), start=1):
        # Only check added lines in the diff (starts with +)
        if not raw_line.startswith('+') or raw_line.startswith('+++'):
            continue
        stripped = raw_line[1:].strip()
        # Match print(...) calls, not inside comments
        if re.search(r'\bprint\s*\(', stripped) and not stripped.startswith('#'):
            actual_line = line_map.get(diff_idx, diff_idx)
            issues.append(f"第 {actual_line} 行存在 `print()` 调用，生产代码中应使用 `logging` 替代: `{stripped[:80]}`")
    
    return "\n".join(issues)

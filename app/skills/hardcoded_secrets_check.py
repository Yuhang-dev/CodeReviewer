import re
import logging
from langchain_core.tools import tool
from app.skills.diff_utils import parse_diff_line_map

logger = logging.getLogger(__name__)

# Common patterns that suggest hardcoded secrets/credentials
SECRET_PATTERNS = [
    # API Keys / Tokens
    (r'(?i)(api_key|apikey|api_token|auth_token|access_token)\s*=\s*["\'][^"\']{8,}["\']', "API Key/Token"),
    # Passwords
    (r'(?i)(password|passwd|pwd|secret)\s*=\s*["\'][^"\']{4,}["\']', "硬编码密码"),
    # AWS credentials
    (r'(?i)(aws_access_key_id|aws_secret_access_key)\s*=\s*["\'][^"\']{10,}["\']', "AWS 凭证"),
    # Generic secrets
    (r'(?i)(secret_key|private_key|signing_key)\s*=\s*["\'][^"\']{8,}["\']', "密钥/签名密钥"),
    # Database connection strings with embedded passwords
    (r'(?i)(mysql|postgresql|redis|mongodb):\/\/\w+:[^@\'"\s]{4,}@', "数据库连接串"),
    # Bearer tokens
    (r'(?i)Bearer\s+[A-Za-z0-9\-_\.]{20,}', "Bearer Token"),
]

# Exclusions: known safe placeholder values
SAFE_PLACEHOLDERS = {
    "your_api_key", "your-api-key", "xxx", "changeme", "placeholder",
    "your_password", "password123", "secret", "example", "test",
    "your_secret", "<your-key>", "<YOUR_TOKEN>", "token_here"
}

@tool
def hardcoded_secrets_check(code_diff: str) -> str:
    """
    检查代码变更中是否存在硬编码的密钥、密码、Token 或数据库连接串等敏感信息。
    这是安全审查的核心检查项，应优先检查。
    返回所有发现的潜在敏感信息的行号及类型，若无则返回空字符串。
    """
    logger.info("[Skill] hardcoded_secrets_check triggered.")
    
    line_map = parse_diff_line_map(code_diff)
    issues = []

    for diff_idx, raw_line in enumerate(code_diff.splitlines(), start=1):
        # Only check added lines in the diff
        if not raw_line.startswith('+') or raw_line.startswith('+++'):
            continue
        stripped = raw_line[1:].strip()
        # Skip comments
        if stripped.startswith('#') or stripped.startswith('//'):
            continue
        
        for pattern, label in SECRET_PATTERNS:
            match = re.search(pattern, stripped)
            if match:
                matched_val = match.group(0)
                # Check if it's a known safe placeholder
                is_safe = any(p.lower() in matched_val.lower() for p in SAFE_PLACEHOLDERS)
                if not is_safe:
                    # Redact the actual secret value for safety
                    redacted = re.sub(r'["\'][^"\']{4,}["\']', '"<REDACTED>"', matched_val)
                    actual_line = line_map.get(diff_idx, diff_idx)
                    issues.append(
                        f"第 {actual_line} 行疑似存在【{label}】硬编码: `{redacted}` — 请改用环境变量或配置中心！"
                    )
                break  # Only report once per line
    
    return "\n".join(issues)

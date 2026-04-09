import logging

logger = logging.getLogger(__name__)

def parse_pr_diff(pr_payload: dict) -> list[str]:
    """
    Simulates parsing the Git diff files from a GitHub Pull Request payload.
    In a real implementation, this would:
    1. Extract PR number and repository from `pr_payload`
    2. Call GitHub API to fetch the PR's patch/diff
    3. Parse the diff to extract changed files, added/removed lines, etc.
    """
    logger.info("Simulating git diff text extraction for PR event...")
    
    # Placeholder diff data
    mock_diff = [
        "diff --git a/app/main.py b/app/main.py\n--- a/app/main.py\n+++ b/app/main.py\n@@ -1,3 +1,4 @@\n+print('Added new feature')",
        "diff --git a/utils/helper.py b/utils/helper.py\n--- a/utils/helper.py\n+++ b/utils/helper.py\n@@ -10,3 +10,4 @@\n-def old_func(): pass\n+def new_func(): return True"
    ]
    
    return mock_diff

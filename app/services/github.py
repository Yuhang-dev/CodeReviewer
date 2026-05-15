import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

async def get_github_client() -> httpx.AsyncClient:
    """Returns an configured httpx client for GitHub API."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
        
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        headers=headers,
        timeout=30.0
    )


import os
import shutil
import subprocess

def shallow_clone_repo(repo_full_name: str, branch: str = "main") -> str:
    """
    Shallow clones a GitHub repository to a local temporary directory for AST analysis.
    Uses the GITHUB_TOKEN for authentication.
    Returns the absolute path to the cloned repository.
    """
    base_cache_dir = "/tmp/repo_caches"
    # Ensure Windows compatibility if running locally
    if os.name == 'nt':
        base_cache_dir = os.path.join(os.environ.get('TEMP', 'C:\\temp'), "repo_caches")
        
    repo_name = repo_full_name.replace("/", "_")
    target_dir = os.path.join(base_cache_dir, f"{repo_name}_{branch}")
    
    # If already exists, delete it to ensure fresh state (since it's shallow and fast)
    if os.path.exists(target_dir):
        logger.info(f"Clearing existing repo cache at {target_dir}...")
        try:
            # On Windows, shutil.rmtree might fail on read-only git files, so handle it
            import stat
            def remove_readonly(func, path, excinfo):
                os.chmod(path, stat.S_IWRITE)
                func(path)
            shutil.rmtree(target_dir, onerror=remove_readonly)
        except Exception as e:
            logger.warning(f"Failed to clear cache directory: {e}")
            
    os.makedirs(base_cache_dir, exist_ok=True)
    
    token = settings.GITHUB_TOKEN
    # Construct authenticated URL
    # format: https://oauth2:{token}@github.com/{repo_full_name}.git
    auth_url = f"https://oauth2:{token}@github.com/{repo_full_name}.git" if token else f"https://github.com/{repo_full_name}.git"
    
    logger.info(f"Shallow cloning {repo_full_name} (branch: {branch}) to {target_dir}...")
    
    try:
        # Run git clone --depth 1
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, auth_url, target_dir],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info("Clone successful.")
        return target_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Git clone failed: {e.stderr}")
        return ""


async def fetch_pr_diff(repo_full_name: str, pr_number: int) -> str:
    """
    Fetches the raw diff patch for a given pull request.
    """
    logger.info(f"Fetching PR diff for {repo_full_name}#{pr_number}...")
    
    async with await get_github_client() as client:
        # To get the diff, we override the Accept header
        response = await client.get(
            f"/repos/{repo_full_name}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"}
        )
        if response.status_code >= 400:
            logger.error(f"GitHub API GET Error [{response.status_code}]: {response.text}")
        response.raise_for_status()
        diff_text = response.text
        logger.info(f"Successfully fetched diff ({len(diff_text)} chars).")
        return diff_text


async def fetch_pr_head_commit(repo_full_name: str, pr_number: int) -> str:
    """
    Fetches the latest commit SHA of the pull request. Required for inline comments.
    """
    logger.info(f"Fetching PR head commit for {repo_full_name}#{pr_number}...")
    async with await get_github_client() as client:
        response = await client.get(
            f"/repos/{repo_full_name}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3+json"}
        )
        response.raise_for_status()
        pr_data = response.json()
        return pr_data["head"]["sha"]

async def fetch_pr_files_data(repo_full_name: str, pr_number: int, commit_id: str) -> list[dict]:
    """
    Fetches the patch diff and full context for each file modified in the PR.
    Returns a list of dictionaries with keys: filename, status, patch, full_content.
    """
    logger.info(f"Fetching structured PR file data for {repo_full_name}#{pr_number}...")
    result_files = []
    
    async with await get_github_client() as client:
        response = await client.get(
            f"/repos/{repo_full_name}/pulls/{pr_number}/files"
        )
        if response.status_code >= 400:
            logger.error(f"Failed to fetch PR files: {response.text}")
            return []
            
        files_data = response.json()
        
        # Language extension map for Qdrant metadata filtering
        EXT_LANG_MAP = {
            ".py": "python", ".go": "go", ".js": "javascript",
            ".ts": "typescript", ".java": "java", ".rb": "ruby",
            ".rs": "rust", ".cpp": "cpp", ".cs": "csharp",
        }
        
        for file_info in files_data:
            filename = file_info.get("filename")
            status = file_info.get("status")
            patch = file_info.get("patch", "")
            
            import os
            ext = os.path.splitext(filename or "")[1].lower()
            language = EXT_LANG_MAP.get(ext, "all")
            
            file_data = {
                "filename": filename,
                "status": status,
                "patch": patch,
                "full_content": "",
                "language": language
            }
            
            # Skip removed files as we don't need their full current content
            if status != "removed":
                file_resp = await client.get(
                    f"/repos/{repo_full_name}/contents/{filename}?ref={commit_id}",
                    headers={"Accept": "application/vnd.github.v3.raw"}
                )
                
                if file_resp.status_code == 200:
                    content = file_resp.text
                    lines = content.split('\n')
                    if len(lines) > 2000:
                        content = "\n".join(lines[:2000]) + "\n... (Content truncated due to length > 2000 lines)"
                    file_data["full_content"] = content
                else:
                    logger.warning(f"Could not fetch full content for {filename}: {file_resp.status_code}")
                    
            result_files.append(file_data)
                
    return result_files
async def post_pr_comment(repo_full_name: str, pr_number: int, comment_body: str):
    """
    Posts a general issue comment to the PR.
    (In GitHub, PRs are backed by Issues, so Issue comments appear in the PR timeline).
    """
    logger.info(f"Posting comment to {repo_full_name}#{pr_number}...")
    
    async with await get_github_client() as client:
        response = await client.post(
            f"/repos/{repo_full_name}/issues/{pr_number}/comments",
            json={"body": comment_body}
        )
        if response.status_code >= 400:
            logger.error(f"GitHub API POST Error [{response.status_code}]: {response.text}")
        response.raise_for_status()
        logger.info(f"Comment successfully posted. URL: {response.json().get('html_url')}")

async def post_pr_review_reply(repo_full_name: str, pr_number: int, comment_id: int, reply_body: str):
    """
    Replies directly inline to a specific review comment thread.
    """
    logger.info(f"Replying to review comment {comment_id} in {repo_full_name}#{pr_number}...")
    
    async with await get_github_client() as client:
        response = await client.post(
            f"/repos/{repo_full_name}/pulls/{pr_number}/comments/{comment_id}/replies",
            json={"body": reply_body}
        )
        if response.status_code >= 400:
            logger.error(f"GitHub API inline reply POST Error [{response.status_code}]: {response.text}")
        response.raise_for_status()
        logger.info(f"Inline reply successfully posted.")

async def fetch_issue_comments(repo_full_name: str, pr_number: int) -> list[str]:
    """
    Fetches the history of issue comments for a PR to provide chat context.
    Returns a list of comment bodies.
    """
    logger.info(f"Fetching issue comments for {repo_full_name}#{pr_number}...")
    async with await get_github_client() as client:
        response = await client.get(
            f"/repos/{repo_full_name}/issues/{pr_number}/comments"
        )
        if response.status_code >= 400:
            logger.error(f"GitHub API GET Comments Error [{response.status_code}]: {response.text}")
            return []
        
        comments_data = response.json()
        # Extract body from each comment
        # We can also filter out bot's own comments or keep them to let AI know its past answers
        return [c.get("body", "") for c in comments_data]

async def post_pr_review(repo_full_name: str, pr_number: int, commit_id: str, review_comments: list[dict]):
    """
    Creates a formal review on a pull request with inline code comments.
    `review_comments` should be a list of dicts: {"path": str, "line": int, "body": str}
    """
    logger.info(f"Posting inline review to {repo_full_name}#{pr_number}...")
    
    payload = {
        "commit_id": commit_id,
        "event": "COMMENT",
        "comments": [
            {
                "path": c.get("file", c.get("path", "")),
                "line": int(c.get("line")),
                "side": "RIGHT",  # Explicitly use new-file line numbers
                "body": c.get("comment", c.get("body", ""))
            }
            for c in review_comments
            if c.get("line") and (c.get("file") or c.get("path"))
        ]
    }
    
    async with await get_github_client() as client:
        response = await client.post(
            f"/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            json=payload
        )
        if response.status_code >= 400:
            logger.error(f"GitHub API POST Review Error [{response.status_code}]: {response.text}")
        response.raise_for_status()
        logger.info(f"Inline Review successfully posted.")

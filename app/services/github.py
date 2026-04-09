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

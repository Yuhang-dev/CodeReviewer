def normalize_display_name(name: str) -> str:
    """
    RAG target:
    - This should be low relevance for SQL, runtime source mutation, and HTTP resilience rules.
    - If score thresholding works, no unrelated guideline should be injected.
    """
    return " ".join(part.capitalize() for part in name.strip().split())


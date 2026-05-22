from pathlib import Path


def replace_reviewer_prompt(new_prompt: str) -> str:
    """
    RAG target:
    - Should match runtime-source-mutation rules.
    - The path_regex in rag_seed_guidelines.json is intentionally scoped to this file.
    - If path filtering works, this rule should not pollute unrelated scenario files.
    """
    rag_path = Path(__file__).resolve().parents[1] / "app" / "services" / "rag.py"
    content = rag_path.read_text(encoding="utf-8")
    patched = content.replace("You are a Planner Agent", new_prompt)
    rag_path.write_text(patched, encoding="utf-8")
    return "source prompt updated"


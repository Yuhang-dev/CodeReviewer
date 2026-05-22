# RAG Retrieval Test Scenarios

This directory contains intentionally small files for testing the CodeReviewer RAG retrieval behavior in a GitHub PR.

Recommended flow:

1. Ingest rules from `rag_seed_guidelines.json` through `POST /knowledge/ingest`.
2. Open a PR that adds or modifies these scenario files.
3. Check the PR-level Agent Trace for retrieved rule category, score, severity, and path filtering behavior.

Expected behavior:

- `rag_case_01_sql_payment.py` should retrieve security / SQL injection / payment rules and escalate to a high tier.
- `rag_case_02_runtime_source_mutation.py` should retrieve runtime source mutation rules only if the rule `path_regex` matches this file path.
- `rag_case_03_allowed_single_arg_add.py` should retrieve an allowance rule and avoid noisy comments if the Critic is working well.
- `rag_case_04_low_relevance.py` should retrieve no meaningful guidelines, or low-score hits should be filtered out.


import codecs

path = '/app/app/services/rag.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

# Fix the Planner f-string
bad_planner = '''You MUST return ONLY a valid JSON object matching the following structure:
{
  "user_requested_tier": "string | null",
  "system_inferred_tier": "string",
  "final_tier": "string",
  "tier_resolution_reason": "string",
  "selected_checks": ["string"],
  "files": [{"path": "string", "language": "string", "risk_reasons": ["string"], "selected_checks": ["string"]}]
}'''

good_planner = '''You MUST return ONLY a valid JSON object matching the following structure:
{{
  "user_requested_tier": "string | null",
  "system_inferred_tier": "string",
  "final_tier": "string",
  "tier_resolution_reason": "string",
  "selected_checks": ["string"],
  "files": [{{"path": "string", "language": "string", "risk_reasons": ["string"], "selected_checks": ["string"]}}]
}}'''

content = content.replace(bad_planner, good_planner)

# Fix the Reviewer f-string
bad_reviewer = '''You MUST return ONLY a valid JSON object matching the following structure:
{
  "findings": [
    {
      "file": "string",
      "line": 123,
      "comment": "string",
      "check": "string",
      "evidence": "string | null",
      "severity": "info | warning | error"
    }
  ]
}'''

good_reviewer = '''You MUST return ONLY a valid JSON object matching the following structure:
{{
  "findings": [
    {{
      "file": "string",
      "line": 123,
      "comment": "string",
      "check": "string",
      "evidence": "string | null",
      "severity": "info | warning | error"
    }}
  ]
}}'''

content = content.replace(bad_reviewer, good_reviewer)

# Fix the Critic f-string
bad_critic = '''You MUST return ONLY a valid JSON object matching the following structure:
{
  "kept": [
    {
      "file": "string",
      "line": 123,
      "comment": "string",
      "check": "string",
      "evidence": "string | null",
      "severity": "info | warning | error"
    }
  ],
  "dropped": [
    {
      "finding": {},
      "reason": "string"
    }
  ]
}'''

good_critic = '''You MUST return ONLY a valid JSON object matching the following structure:
{{
  "kept": [
    {{
      "file": "string",
      "line": 123,
      "comment": "string",
      "check": "string",
      "evidence": "string | null",
      "severity": "info | warning | error"
    }}
  ],
  "dropped": [
    {{
      "finding": {{}},
      "reason": "string"
    }}
  ]
}}'''

content = content.replace(bad_critic, good_critic)

with codecs.open(path, 'w', 'utf-8') as f:
    f.write(content)
print("F-string syntax fixed")

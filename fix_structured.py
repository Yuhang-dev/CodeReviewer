import sys
import codecs
import re

try:
    rag_path = '/app/app/services/rag.py'
    
    with codecs.open(rag_path, 'r', 'utf-8') as f:
        content = f.read()

    # We need to replace three sections:
    # 1. planner_step:
    # llm = init_llm().with_structured_output(PlannerOutput)
    # response: PlannerOutput = llm.invoke(...)
    
    # 2. reviewer_step:
    # llm = init_llm().with_structured_output(ReviewerOutput)
    # response: ReviewerOutput = llm.invoke(...)
    
    # 3. critic_step:
    # llm = init_llm().with_structured_output(CriticDecision)
    # decision: CriticDecision = llm.invoke(...)
    
    # Let's just rewrite the three functions entirely, it's safer.
    # I will extract the blocks and replace them.

    # But we can also just use regex to replace specific lines.
    
    # Planner Step Fix
    content = content.replace(
        "llm = init_llm().with_structured_output(PlannerOutput)",
        "llm = init_llm().bind(response_format={'type': 'json_object'})"
    )
    content = content.replace(
        "5. Provide a plan for this specific file in the `files` list.",
        "5. Provide a plan for this specific file in the `files` list.\n\nYou MUST return ONLY a valid JSON object matching the following structure:\n{\n  \"user_requested_tier\": \"string | null\",\n  \"system_inferred_tier\": \"string\",\n  \"final_tier\": \"string\",\n  \"tier_resolution_reason\": \"string\",\n  \"selected_checks\": [\"string\"],\n  \"files\": [{\"path\": \"string\", \"language\": \"string\", \"risk_reasons\": [\"string\"], \"selected_checks\": [\"string\"]}]\n}"
    )
    content = content.replace(
        "plan: PlannerOutput = llm.invoke([HumanMessage(content=prompt)])\n        plan_dict = plan.dict()",
        """response = llm.invoke([HumanMessage(content=prompt)])
        import json
        raw_text = response.content.strip()
        if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
        plan_dict = json.loads(raw_text)
        plan = PlannerOutput(**plan_dict)
        plan_dict = plan.dict()"""
    )
    
    # Reviewer Step Fix
    content = content.replace(
        "llm = init_llm().with_structured_output(ReviewerOutput)",
        "llm = init_llm().bind(response_format={'type': 'json_object'})"
    )
    content = content.replace(
        "【代码 Diff 变更】:\\n{annotate_diff_with_line_numbers(diff_text)}\\n",
        "【代码 Diff 变更】:\\n{annotate_diff_with_line_numbers(diff_text)}\\n\\nYou MUST return ONLY a valid JSON object matching the following structure:\\n{\\n  \"findings\": [\\n    {\\n      \"file\": \"string\",\\n      \"line\": 123,\\n      \"comment\": \"string\",\\n      \"check\": \"string\",\\n      \"evidence\": \"string | null\",\\n      \"severity\": \"info | warning | error\"\\n    }\\n  ]\\n}"
    )
    content = content.replace(
        "response: ReviewerOutput = llm.invoke([HumanMessage(content=prompt)])\n        raw_reviews = [f.dict() for f in response.findings]",
        """response = llm.invoke([HumanMessage(content=prompt)])
        import json
        raw_text = response.content.strip()
        if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
        resp_dict = json.loads(raw_text)
        resp_obj = ReviewerOutput(**resp_dict)
        raw_reviews = [f.dict() for f in resp_obj.findings]"""
    )
    
    # Critic Step Fix
    content = content.replace(
        "llm = init_llm().with_structured_output(CriticDecision)",
        "llm = init_llm().bind(response_format={'type': 'json_object'})"
    )
    content = content.replace(
        "Output which to keep and which to drop (with reasons).",
        "Output which to keep and which to drop (with reasons).\nYou MUST return ONLY a valid JSON object matching the following structure:\n{\n  \"kept\": [\n    {\n      \"file\": \"string\",\n      \"line\": 123,\n      \"comment\": \"string\",\n      \"check\": \"string\",\n      \"evidence\": \"string | null\",\n      \"severity\": \"info | warning | error\"\n    }\n  ],\n  \"dropped\": [\n    {\n      \"finding\": {},\n      \"reason\": \"string\"\n    }\n  ]\n}"
    )
    content = content.replace(
        "decision: CriticDecision = llm.invoke([HumanMessage(content=prompt)])\n            final_reviews = [f.dict() for f in decision.kept]\n            for d in decision.dropped:\n                dropped.append(d)",
        """response = llm.invoke([HumanMessage(content=prompt)])
            import json
            raw_text = response.content.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
            resp_dict = json.loads(raw_text)
            decision = CriticDecision(**resp_dict)
            final_reviews = [f.dict() for f in decision.kept]
            for d in decision.dropped:
                dropped.append(d)"""
    )

    with codecs.open(rag_path, 'w', 'utf-8') as f:
        f.write(content)
        
    print('Successfully patched structured outputs in rag.py')
except Exception as e:
    print(e)

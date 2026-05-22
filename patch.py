import sys
import codecs

try:
    rag_path = '/app/app/services/rag.py'
    nodes_path = '/app/patch_nodes.py'
    
    with codecs.open(rag_path, 'r', 'utf-8') as f:
        content = f.read()

    with codecs.open(nodes_path, 'r', 'utf-8') as f:
        replacement_nodes = f.read()

    # Find the start of review_code_step
    start_str = 'def review_code_step(state: AgentState):'
    end_str = 'graph = build_review_graph()'
    
    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    
    if start_idx == -1 or end_idx == -1:
        print('Could not find start or end block.')
        sys.exit(1)
        
    end_idx += len(end_str)
    
    graph_compilation = '''
def build_review_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("planner", planner_step)
    workflow.add_node("retrieve", retrieve_step)
    workflow.add_node("reviewer", reviewer_step)
    workflow.add_node("critic", critic_step)
    workflow.add_node("finalize", finalize_review_step)
    
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "retrieve")
    workflow.add_edge("retrieve", "reviewer")
    workflow.add_edge("reviewer", "critic")
    workflow.add_edge("critic", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()

graph = build_review_graph()
'''
    
    replacement = replacement_nodes + '\n\n' + graph_compilation
    
    new_content = content[:start_idx] + replacement + content[end_idx:]
    
    with codecs.open(rag_path, 'w', 'utf-8') as f:
        f.write(new_content)
        
    print('Successfully replaced nodes in rag.py')
except Exception as e:
    print(e)

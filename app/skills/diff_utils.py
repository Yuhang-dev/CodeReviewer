import re
import logging

logger = logging.getLogger(__name__)

def annotate_diff_with_line_numbers(patch: str) -> str:
    """
    Takes a raw unified diff patch and annotates each line with its 
    actual file line number (new-file side). This ensures the LLM outputs 
    correct line numbers that match what GitHub Review API expects.
    
    Example output:
        @@ -10,5 +10,7 @@
        L10:  existing line
        L11: +new added line
        L12: +another new line
        L13:  context line
    """
    if not patch:
        return ""
    
    annotated_lines = []
    current_new_line = 0
    
    for line in patch.splitlines():
        # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
        hunk_match = re.match(r'^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@', line)
        if hunk_match:
            current_new_line = int(hunk_match.group(1))
            annotated_lines.append(line)  # Keep the hunk header as-is
            continue
        
        if line.startswith('-'):
            # Deleted lines don't exist in the new file, no line number
            annotated_lines.append(f"     : {line}")
        elif line.startswith('+'):
            # Added lines get the new file line number
            annotated_lines.append(f"L{current_new_line:>4}: {line}")
            current_new_line += 1
        else:
            # Context lines (unchanged) also advance the new file line counter
            annotated_lines.append(f"L{current_new_line:>4}: {line}")
            current_new_line += 1
    
    return "\n".join(annotated_lines)

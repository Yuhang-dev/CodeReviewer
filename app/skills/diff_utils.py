import re
from typing import Optional


def parse_diff_line_map(diff_text: str) -> dict[int, int]:
    """
    Parses a unified diff and returns a mapping from:
        diff_index (1-based line index in the whole diff string)
        -> new_file_line (actual line number in the new/right-hand file)

    Only '+' lines (added lines) get a valid new_file_line.
    Context lines (' ') also get a mapping.
    '-' lines (removed lines) map to None in the underlying logic but are excluded here.

    Example:
        diff_map = parse_diff_line_map(diff_text)
        new_file_line = diff_map.get(diff_index)  # None if it's a removed line
    """
    line_map: dict[int, Optional[int]] = {}
    new_file_line = 0

    for diff_index, raw_line in enumerate(diff_text.splitlines(), start=1):
        # Hunk header: @@ -old_start,old_count +new_start,new_count @@
        hunk_match = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', raw_line)
        if hunk_match:
            new_file_line = int(hunk_match.group(1)) - 1  # Will be incremented before use
            line_map[diff_index] = None
            continue

        if raw_line.startswith('+') and not raw_line.startswith('+++'):
            # Added line — belongs to the new file
            new_file_line += 1
            line_map[diff_index] = new_file_line
        elif raw_line.startswith('-') and not raw_line.startswith('---'):
            # Removed line — does not exist in new file
            line_map[diff_index] = None
        else:
            # Context line or header — exists in both files; advance new file pointer
            new_file_line += 1
            line_map[diff_index] = new_file_line

    return {k: v for k, v in line_map.items() if v is not None}

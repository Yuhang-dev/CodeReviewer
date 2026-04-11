import re
from typing import Optional


def parse_diff_line_map(diff_text: str) -> dict[int, int]:
    """
    Parses a unified diff and returns a mapping from:
        diff_index (1-based line index in the whole diff string)
        -> new_file_line (actual line number in the new/right-hand file)

    Per the unified diff spec:
      - Lines starting with ' ' (space) are context lines
      - Lines starting with '+' (not '+++') are added lines
      - Lines starting with '-' (not '---') are removed lines
      - Lines starting with '@@ ... @@' are hunk headers (reset new_file_line)
      - All other lines (file headers, custom prefixes, etc.) are SKIPPED
    """
    line_map: dict[int, int] = {}
    new_file_line = 0

    for diff_index, raw_line in enumerate(diff_text.splitlines(), start=1):
        # Hunk header: @@ -old_start,old_count +new_start,new_count @@
        hunk_match = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', raw_line)
        if hunk_match:
            new_file_line = int(hunk_match.group(1)) - 1  # Will +1 before next use
            continue  # Hunk headers don't map to a file line

        if raw_line.startswith('+') and not raw_line.startswith('+++'):
            # Added line — exists only in new file
            new_file_line += 1
            line_map[diff_index] = new_file_line

        elif raw_line.startswith('-') and not raw_line.startswith('---'):
            # Removed line — does not exist in new file, don't increment
            pass

        elif raw_line.startswith(' '):
            # Context line (MUST start with a literal space per unified diff spec)
            new_file_line += 1
            line_map[diff_index] = new_file_line

        # else: skip — file headers (---, +++, diff --git, index, File: etc.)

    return line_map

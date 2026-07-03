from __future__ import annotations

from typing import Dict, List, Optional

def table(headers: List[str], rows: List[List[str]], max_widths: Optional[Dict[int, int]] = None) -> str:
    """Format data as ASCII table."""
    if not rows:
        return "No data"

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    if max_widths:
        for i, max_w in max_widths.items():
            if i < len(widths):
                widths[i] = min(widths[i], max_w)

    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "  ".join("-" * w for w in widths)
    row_lines = []
    for row in rows:
        row_lines.append(
            "  ".join(str(cell)[: widths[i]].ljust(widths[i]) for i, cell in enumerate(row))
        )

    return "\n".join([header_line, separator] + row_lines)

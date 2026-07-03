"""Analyse completed terminal sessions and persist behaviour reports."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from agentfactor import constants
from agentfactor.clients.database import TerminalAnalysis, WorkItem as WorkItemRow, session_scope

LOG = logging.getLogger(__name__)

# ── log parsing ────────────────────────────────────────────────────────────────
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_TOOL_NAME_RE = re.compile(r"[●⏺]\s*([A-Z]\w+)\s*\(")
_FILE_FROM_TOOL_RE = re.compile(
    r"[●⏺]\s*(?:Read|Edit|Write|Glob|Grep)\s*\([\"']?([/~.\w][\w.\-/\\]+)[\"']?"
)
_BASH_CMD_RE = re.compile(r"[●⏺]\s*Bash\s*\([\"']?([^\n\"']{5,150})")

# ── conversation parsing ───────────────────────────────────────────────────────
# Human input: a line that starts with "> " followed by non-whitespace content
_HUMAN_INPUT_RE = re.compile(r"^>\s+(\S.*)$")
# Tool call: ⏺/● ToolName(...)
_TOOL_CALL_LINE_RE = re.compile(r"^[●⏺]\s+([A-Z]\w+)\s*\((.{0,300})")
# Agent text: ⏺/● followed by non-tool content
_AGENT_LINE_RE = re.compile(r"^[●⏺]\s+(.+)$")
# Processing spinners — skip these lines
_SPINNER_RE = re.compile(r"^[\s✶✢✽⠙⠹⠸⠼⠴⠦⠧⠇⠏►◆◇◈\-–—.]{1,4}$")
# Indented result lines that follow a tool call
_INDENT_RE = re.compile(r"^[ \t]{2,}")

_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-[rf]+\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bcurl\b.*\|\s*(?:sh|bash)\b"),
    re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
    re.compile(r"\bDELETE\b.*\bFROM\b", re.IGNORECASE),
]

# Store at most 800 conversation turns per terminal
_MAX_CONV_TURNS = 800


class AnalysisService:
    """Parse terminal logs and build a persisted behaviour analysis record."""

    def analyze_terminal(
        self,
        terminal_id: str,
        session_name: Optional[str] = None,
        window_name: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Optional[dict]:
        """Parse the terminal log and persist a TerminalAnalysis row. Returns the record or None."""
        log_path = constants.TERMINAL_LOG_DIR / f"{terminal_id}.log"
        if not log_path.exists():
            LOG.debug("No log file for terminal %s — skipping analysis", terminal_id)
            return None

        try:
            log_content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            LOG.warning("Could not read log for terminal %s", terminal_id)
            return None

        tool_stats, files_touched, commands_run, risk_flags = self._parse_log(log_content)
        work_item_id, compliance_summary = self._compliance_check(
            terminal_id, files_touched, risk_flags, log_content
        )

        raw_log = log_content

        # Parse conversation turns
        clean_log = _ANSI_ESCAPE_RE.sub("", log_content)
        conversation_turns = self._parse_conversation(clean_log)

        with session_scope() as db:
            existing = db.query(TerminalAnalysis).filter_by(terminal_id=terminal_id).first()
            if existing:
                db.delete(existing)
                db.flush()

            row = TerminalAnalysis(
                terminal_id=terminal_id,
                session_name=session_name,
                window_name=window_name,
                provider=provider,
                tool_stats=json.dumps(tool_stats),
                files_touched=json.dumps(files_touched),
                commands_run=json.dumps(commands_run[:60]),
                risk_flags=json.dumps(risk_flags),
                work_item_id=work_item_id,
                compliance_summary=json.dumps(compliance_summary) if compliance_summary else None,
                line_count=log_content.count("\n"),
                raw_log=raw_log,
                conversation_turns=json.dumps(conversation_turns[:_MAX_CONV_TURNS]),
                review_status="pending",
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            result = self._serialize(row)

        LOG.info(
            "Saved analysis for terminal %s: %d tool calls, %d files, %d risk flags, %d conv turns",
            terminal_id,
            sum(tool_stats.values()),
            len(files_touched),
            len(risk_flags),
            len(conversation_turns),
        )
        return result

    def get_analysis(self, terminal_id: str) -> Optional[dict]:
        with session_scope() as db:
            row = db.query(TerminalAnalysis).filter_by(terminal_id=terminal_id).first()
            return self._serialize(row) if row else None

    def list_analyses(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 200))
        with session_scope() as db:
            rows = (
                db.query(TerminalAnalysis)
                .order_by(TerminalAnalysis.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._serialize(r) for r in rows]

    def get_raw_log(self, terminal_id: str) -> Optional[str]:
        """Return the stored raw log text for a terminal, or None if not found."""
        with session_scope() as db:
            row = db.query(TerminalAnalysis).filter_by(terminal_id=terminal_id).first()
            if row is None:
                return None
            # If raw_log was stored, return it; otherwise fall back to reading the file
            if row.raw_log:
                return row.raw_log
        # Fallback: try the log file directly
        log_path = constants.TERMINAL_LOG_DIR / f"{terminal_id}.log"
        if log_path.exists():
            try:
                return log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        return None

    def get_conversation(self, terminal_id: str) -> Optional[list[dict]]:
        """Return parsed conversation turns for a terminal."""
        with session_scope() as db:
            row = db.query(TerminalAnalysis).filter_by(terminal_id=terminal_id).first()
            if row is None:
                return None
            return json.loads(row.conversation_turns or "[]")

    def search_history(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across stored raw logs and metadata. Returns matching analyses."""
        if not query or not query.strip():
            return []
        limit = max(1, min(limit, 100))
        q = query.strip().lower()
        results: list[dict] = []
        with session_scope() as db:
            # SQLite doesn't have built-in FTS here; use LIKE on raw_log + files/commands
            rows = (
                db.query(TerminalAnalysis)
                .order_by(TerminalAnalysis.created_at.desc())
                .limit(500)
                .all()
            )
            for row in rows:
                if self._row_matches(row, q):
                    serialized = self._serialize(row, include_log=False)
                    serialized["match_excerpt"] = self._excerpt(row, q)
                    results.append(serialized)
                if len(results) >= limit:
                    break
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_log(
        self, content: str
    ) -> tuple[dict[str, int], list[str], list[str], list[str]]:
        tool_stats: dict[str, int] = {}
        files_seen: dict[str, bool] = {}
        commands: list[str] = []
        risk_flags: list[str] = []

        for match in _TOOL_NAME_RE.finditer(content):
            name = match.group(1)
            tool_stats[name] = tool_stats.get(name, 0) + 1

        for match in _FILE_FROM_TOOL_RE.finditer(content):
            path = match.group(1).strip()
            if path and len(path) > 2:
                files_seen[path] = True

        for match in _BASH_CMD_RE.finditer(content):
            cmd = match.group(1).strip()
            if cmd:
                commands.append(cmd)
                for pat in _DESTRUCTIVE_PATTERNS:
                    if pat.search(cmd):
                        flag = f"Destructive command: {cmd[:100]}"
                        if flag not in risk_flags:
                            risk_flags.append(flag)

        return tool_stats, list(files_seen.keys()), commands, risk_flags

    def _parse_conversation(self, clean_content: str) -> list[dict]:
        """
        Parse ANSI-stripped terminal log into conversation turns.

        Returns a list of dicts with keys:
          type: 'human' | 'agent' | 'tool_call' | 'tool_result'
          content: str
          tool_name: str  (only for tool_call)
          index: int
        """
        turns: list[dict] = []
        lines = clean_content.split("\n")

        pending_agent_lines: list[str] = []
        in_tool_result = False  # True when accumulating indented tool result lines
        tool_result_lines: list[str] = []

        def flush_agent():
            nonlocal pending_agent_lines
            text = "\n".join(pending_agent_lines).strip()
            if text:
                turns.append({"type": "agent", "content": text, "index": len(turns)})
            pending_agent_lines = []

        def flush_tool_result():
            nonlocal in_tool_result, tool_result_lines
            text = "\n".join(tool_result_lines).strip()
            if text:
                turns.append({"type": "tool_result", "content": text, "index": len(turns)})
            in_tool_result = False
            tool_result_lines = []

        for raw_line in lines:
            line = raw_line.rstrip()

            # Skip blank lines and pure spinners
            if not line or _SPINNER_RE.match(line):
                if in_tool_result:
                    # blank line ends a tool result block
                    flush_tool_result()
                continue

            # Indented line after a tool call → tool result
            if in_tool_result:
                if _INDENT_RE.match(raw_line) or not line.startswith((">", "⏺", "●")):
                    tool_result_lines.append(line.strip())
                    continue
                else:
                    flush_tool_result()

            # Human input: "> message"
            m = _HUMAN_INPUT_RE.match(line)
            if m:
                flush_agent()
                turns.append({"type": "human", "content": m.group(1).strip(), "index": len(turns)})
                continue

            # Tool call: ⏺ ToolName(...)
            m = _TOOL_CALL_LINE_RE.match(line)
            if m:
                flush_agent()
                tool_name = m.group(1)
                tool_args = m.group(2).rstrip(")").strip()
                turns.append({
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "content": tool_args,
                    "index": len(turns),
                })
                # Next indented lines are the result
                in_tool_result = True
                tool_result_lines = []
                continue

            # Agent text: ⏺ text (not a tool call)
            m = _AGENT_LINE_RE.match(line)
            if m:
                pending_agent_lines.append(m.group(1))
                continue

            # Bare ">" alone = idle prompt, ignore
            if line.strip() == ">":
                continue

            # Everything else: append to current agent block if we're in one
            if pending_agent_lines:
                pending_agent_lines.append(line)

        # Flush leftovers
        if in_tool_result:
            flush_tool_result()
        flush_agent()

        return turns

    def _compliance_check(
        self,
        terminal_id: str,
        files_touched: list[str],
        risk_flags: list[str],
        log_content: str,
    ) -> tuple[Optional[str], Optional[dict]]:
        """Find an owned work item and return (work_item_id, compliance_dict)."""
        with session_scope() as db:
            wi = (
                db.query(WorkItemRow)
                .filter(WorkItemRow.owner_terminal_id == terminal_id)
                .first()
            )
            if wi is None:
                return None, None

            work_item_id = wi.id
            files_of_interest: list[str] = (
                json.loads(wi.files_of_interest)
                if isinstance(wi.files_of_interest, str)
                else (wi.files_of_interest or [])
            )
            acceptance_criteria: list[str] = (
                json.loads(wi.acceptance_criteria)
                if isinstance(wi.acceptance_criteria, str)
                else (wi.acceptance_criteria or [])
            )
            work_item_title = wi.title

        touched_set = set(files_touched)
        foi_set = set(files_of_interest)
        covered = touched_set & foi_set

        criteria_hits = []
        for criterion in acceptance_criteria:
            words = [w for w in re.split(r"\W+", criterion) if len(w) > 4]
            hit = bool(words) and any(w.lower() in log_content.lower() for w in words[:6])
            criteria_hits.append({"criterion": criterion, "evidence_found": hit})

        summary = {
            "work_item_title": work_item_title,
            "files_of_interest": {
                "total": len(foi_set),
                "covered": len(covered),
                "pct": round(len(covered) / len(foi_set) * 100) if foi_set else 100,
                "covered_files": sorted(covered),
                "missed_files": sorted(foi_set - touched_set),
            },
            "acceptance_criteria": criteria_hits,
            "has_risk_flags": bool(risk_flags),
        }
        return work_item_id, summary

    @staticmethod
    def _row_matches(row: TerminalAnalysis, query: str) -> bool:
        """Check if a row matches the search query."""
        searchable = " ".join(filter(None, [
            row.raw_log or "",
            row.window_name or "",
            row.session_name or "",
            row.files_touched or "",
            row.commands_run or "",
        ])).lower()
        return query in searchable

    @staticmethod
    def _excerpt(row: TerminalAnalysis, query: str) -> str:
        """Return a short excerpt of raw_log around the first match."""
        if not row.raw_log:
            return ""
        idx = row.raw_log.lower().find(query)
        if idx == -1:
            return ""
        start = max(0, idx - 80)
        end = min(len(row.raw_log), idx + 160)
        snippet = row.raw_log[start:end].replace("\n", " ").strip()
        return f"…{snippet}…" if start > 0 else f"{snippet}…"

    @staticmethod
    def _serialize(row: TerminalAnalysis, include_log: bool = True) -> dict:
        data = {
            "id": row.id,
            "terminal_id": row.terminal_id,
            "session_name": row.session_name,
            "window_name": row.window_name,
            "provider": row.provider,
            "tool_stats": json.loads(row.tool_stats or "{}"),
            "files_touched": json.loads(row.files_touched or "[]"),
            "commands_run": json.loads(row.commands_run or "[]"),
            "risk_flags": json.loads(row.risk_flags or "[]"),
            "work_item_id": row.work_item_id,
            "compliance_summary": json.loads(row.compliance_summary) if row.compliance_summary else None,
            "line_count": row.line_count,
            "conversation_turn_count": len(json.loads(row.conversation_turns or "[]")),
            "has_raw_log": bool(row.raw_log),
            "llm_review": json.loads(row.llm_review) if row.llm_review else None,
            "review_status": row.review_status,
            "review_model": row.review_model,
            "review_provider_id": row.review_provider_id,
            "review_error": row.review_error,
            "reviewed_at": str(row.reviewed_at) if row.reviewed_at else None,
            "created_at": str(row.created_at),
        }
        if include_log:
            data["raw_log"] = row.raw_log or ""
        return data

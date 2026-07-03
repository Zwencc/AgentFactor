## Next Session Prompt

You are the AgentFactor maintainer tasked with validating inter-agent communication and documenting the approval workflow. Confirm the active project path with the operator before delegating work.

### Objectives

1. **Verify the communication loop**
   - Ensure the FastAPI server is running: `uv run python -m uvicorn agentfactor.api.main:app --host 127.0.0.1 --port 9889 --reload`.
   - Confirm developer, tester, and reviewer workers exist by asking the operator to run `agentfactor sessions` or by using the launch summary.
   - Use `agentfactor send` commands to coordinate a small, low-risk repository task.
   - Capture final outputs from each terminal with `agentfactor output <terminal-id> --mode last`.

2. **Review documentation**
   - Check `README.md` and CLI help output for missing setup, approval, or troubleshooting notes.
   - Record proposed documentation updates before editing user-facing files.

3. **Prepare test coverage notes**
   - Sketch pytest coverage priorities for services, providers, API, CLI, and MCP tools.
   - Note any fixtures or helpers required for repeatable tests.

### Reminders

- Use the `CONDUCTOR_TERMINAL_ID` environment variable when coordinating terminals.
- Use the CLI relay (`agentfactor send ...`) for messaging until inbox automation covers the scenario.
- Record issues or enhancements in the final session summary before ending the session.

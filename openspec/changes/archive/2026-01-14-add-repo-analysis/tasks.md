## 1. Test Harness + Sub-Agent
- [ ] 1.1 Create `repo_analysis/` module structure
- [ ] 1.2 Implement `repo_analysis_agent.py` — standalone sub-agent entry point (no Django models/streaming) that takes file:// URL, returns JSON
- [ ] 1.3 Configure sub-agent tool allow-list (Bash, Read, LS, Glob, Grep) separate from main agent/MCP tools
- [ ] 1.4 Write initial `system_prompt.md` with output schema and evidence discipline
- [ ] 1.5 Implement `test_repo_analysis.py` with CLI interface (--app flag for single app, default runs all)

## 2. Output Schema
- [ ] 2.1 Define Pydantic model for analysis output (including evidence fields)
- [ ] 2.2 Define reference app expectations dictionary
- [ ] 2.3 Implement JSON validation in test harness
- [ ] 2.4 Handle malformed responses gracefully (retry or error)

## 3. Iterate on System Prompt
- [ ] 3.1 Test against `django_postgres_app` — tune prompt until output matches
- [ ] 3.2 Test against `fastapi_app` — stateless app, no database
- [ ] 3.3 Test against `nextjs_app` — Node ecosystem
- [ ] 3.4 Test against `job_processor` — worker pattern, SQS/S3
- [ ] 3.5 Test against `phoenix_app` — Elixir detection
- [ ] 3.6 Run full suite, fix any regressions

## 4. Main Agent Integration
- [ ] 4.1 Create sub-agent spawning mechanism from main agent
- [ ] 4.2 Wire file:// URL from user input to sub-agent
- [ ] 4.3 Extract structured output from sub-agent response

## 5. User Review
- [ ] 5.1 Render analysis findings in chat (human-readable)
- [ ] 5.2 If user gives corrections, main agent re-spawns sub-agent with new instructions

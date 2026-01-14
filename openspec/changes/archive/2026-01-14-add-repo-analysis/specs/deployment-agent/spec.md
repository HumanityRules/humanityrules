## ADDED Requirements

### Requirement: Repository Analysis Sub-Agent

The system SHALL provide repository analysis via an LLM sub-agent that examines repositories and produces structured findings.

This sub-agent is a new capability and does not replace the existing `inspect_repository` tool.

#### Scenario: Sub-agent receives local file URL only
- **WHEN** repository analysis is triggered
- **THEN** the sub-agent receives only a file:// URL pointing to a local directory
- **AND** the sub-agent inspects the repository at that path (no cloning needed)

#### Scenario: Sub-agent uses a different tool set than the main agent
- **WHEN** the sub-agent analyzes a repository
- **THEN** it is configured with a tool allow-list appropriate for repository inspection
- **AND** the allow-list includes Bash for script execution and Read/LS/Glob/Grep for file inspection
- **AND** it does not rely on the main agent's MCP tool allow-list

#### Scenario: Sub-agent is isolated from Django models and streaming
- **WHEN** the sub-agent runs
- **THEN** it does not access Django models
- **AND** it does not perform chat streaming or message persistence
- **AND** it returns results directly to the caller (test harness or main agent)

#### Scenario: Sub-agent is invoked via SDK native sub-agent mechanism
- **WHEN** repository analysis is executed
- **THEN** the caller invokes the sub-agent via the Claude Agent SDK's native sub-agent support (agents + Task tool)
- **AND** the sub-agent produces the final structured JSON result

#### Scenario: Detect framework and language
- **WHEN** the sub-agent analyzes a repository
- **THEN** it detects the primary language (python, node, elixir, go, etc.)
- **AND** it detects the framework (django, fastapi, nextjs, phoenix, etc.)
- **AND** each claim includes evidence referencing repository paths and excerpts

#### Scenario: Detect service configuration
- **WHEN** the sub-agent analyzes a repository
- **THEN** it identifies the service type (web, worker, scheduled_task)
- **AND** it determines the run command if it can be inferred from evidence
- **AND** it detects the port and health check path (for web services) if it can be inferred from evidence
- **AND** unknown or ambiguous fields are returned as null with caveats

#### Scenario: Detect infrastructure dependencies
- **WHEN** the sub-agent analyzes a repository
- **THEN** it identifies required datastores (postgres, redis, mysql, etc.) when evidenced
- **AND** it identifies AWS service dependencies (s3, sqs, dynamodb, etc.) when evidenced

#### Scenario: Extract environment variables
- **WHEN** the sub-agent analyzes a repository
- **THEN** it identifies environment variables from .env.example or config patterns
- **AND** it separates required from optional when it can be inferred from evidence
- **AND** it documents the purpose of each variable when it can be inferred from evidence

#### Scenario: Produce structured output with evidence
- **WHEN** analysis completes
- **THEN** the sub-agent returns JSON with description, language, framework, service config, dependencies, env vars, caveats, and evidence
- **AND** evidence is included for major claims (framework, entrypoint/run command, port/health path, dependencies)

#### Scenario: User reviews findings via conversation loop
- **WHEN** analysis findings are presented to the user
- **THEN** the user can accept implicitly by proceeding, or provide corrections in natural language
- **AND** if corrections are provided, the main agent re-spawns the sub-agent with the new instructions

#### Scenario: One repo = one app
- **WHEN** the sub-agent analyzes a repository
- **THEN** it assumes exactly one deployable application per repository
- **AND** monorepo detection and selection is not supported

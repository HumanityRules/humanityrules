# Project Context

Project-specific constraints for writing specs. For OpenSpec syntax, see `openspec/AGENTS.md`. For code conventions and architecture, see the root `AGENTS.md`.

This file will grow as the project grows.

## Constraints

Any spec touching these areas must reflect these rules:

### Security
- External ID required for all cross-account AssumeRole
- Per-app secret isolation (task roles scoped to own secrets)
- Secrets never in CloudFormation templates
- Private subnets for all workloads

# Change: Add Repository Analysis Sub-Agent

## Why
The deployment agent needs to understand what a repository contains before it can generate Dockerfiles, create deployment plans, or provision infrastructure. This analysis must be accurate, evidence-based, and transparent.

## What Changes
- New sub-agent that analyzes repositories and produces structured findings
- Sub-agent uses standard Agent SDK tools + code execution (no custom tools)
- Output feeds into Dockerfile generation (Step 3) and Deployment Plan (Step 5)

## Impact
- Affected specs: deployment-agent (new capability)
- Affected code: agent system, sub-agent orchestration
- Dependencies: Agent SDK with Bash, Read, LS, Glob, Grep tools

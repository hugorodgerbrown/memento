# 0011. Capture is proactive, and the policy lives in the tool description

Status: accepted, September 2026

## Context

Most content is casual logging with no "remember". Capture must work from any MCP client, without a Project or custom prompt. MCP server `instructions` are ignored by some clients, and claude.ai reportedly truncates tool descriptions at about 500 characters.

## Decision

`remember` saves what users say about their own life without being asked. The policy, with its exclusions, sits in the first 500 characters of its description; field rules go in schema field descriptions; reminders ride in tool results. Every save returns a receipt. A duplicate guard stores the same words, kind and day once.

## Consequences

Behaviour depends on each client's model, so it is measured with `docs/evals/capture-policy.json` per client. Over-capture is managed with exclusions, receipts and one-step undo.

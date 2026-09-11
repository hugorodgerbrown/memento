# 0005. Bearer token first, then OAuth 2.1

Status: accepted, September 2026

## Context

Capture happens from several clients. claude.ai's custom connectors only offer OAuth; Claude Code accepts custom headers; ChatGPT registers dynamically.

## Decision

Day one: a static bearer token for Claude Code. Then OAuth 2.1 (authorisation code + PKCE, protected-resource metadata, dynamic client registration) via django-oauth-toolkit, with scopes `memento:read`, `memento:write`, `memento:forget`.

## Consequences

Dogfooding starts before the hardest engineering. `forget` can be withheld from a client by scope.

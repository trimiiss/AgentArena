# Sample agent

A rule-based customer-support agent that speaks the [AgentArena Agent Protocol](../../contracts/agent-protocol/README.md). It lets you try the platform without an LLM key, and it shows how little an integration needs: one HTTP endpoint and no AgentArena code.

Its behaviour depends on the path:

| Path | Behaviour |
| --- | --- |
| `/agents/v2` | Careful. Follows the store policy and handles tool failures. |
| `/agents/v1` | An earlier version with realistic flaws. It ignores limits and invents answers when a tool fails. |
| `/agents/secure` | Same as v2, but requires a bearer token. |
| `/agents/flaky` | Same as v2, but a share of first attempts return 503. |
| `/agents/slow` | Same as v2, with several seconds of latency. |
| `/agents/hang` | Never answers, to exercise timeouts. |
| `/agents/broken` | Returns a body that violates the protocol. |

Token counts are estimated from text length, since no model is involved. These results describe this rule engine, not any real model.

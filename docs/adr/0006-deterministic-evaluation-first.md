# 0006. Deterministic evaluation decides the score

Status: accepted

## Context

Benchmarks are only useful if they can be compared over time. An LLM judge can vary between calls, its model changes, and it reads text produced by the agent under test, which could try to influence it. Yet qualitative feedback is valuable.

The first version of the scoring also had a real flaw. It passed a scenario when a weighted average cleared 0.7. Agents that skipped a required tool, or claimed success after a failure, could score 0.79 and pass, because latency and tone checks lifted the average. See [journal F1](../journal/f1.md).

## Decision

Scores come only from deterministic evaluators, which run only when a scenario defines what they need. A scenario passes when all of these hold:

1. Every **requirement** check passes. These are the things the scenario says must be true: required information, tool calls, tool order, forbidden tools and phrases, business rules, structured output, failure handling and task status.
2. The weighted score reaches the pass threshold.
3. Every critical dimension scores 1.0. Policy is critical by default.

**Advisory** checks (similarity, response quality, latency, cost) lower the score without failing the scenario.

Tool failure handling is judged from tool results, not wording. Saying "done" after a failed call is not recovery.

The optional LLM judge runs after the scores commit. Its output is stored separately and never enters a benchmark.

## Alternatives

- **LLM judge as the main score.** Flexible and cheap to set up, but not repeatable, and open to manipulation by the agent under test.
- **Weighted average only.** The original design. Rejected because averages hide decisive failures.
- **Embedding similarity for correctness.** Better for meaning, but it adds a model dependency. Lexical similarity is used instead, and it is advisory for that reason.

## Consequences

- Reruns give the same score for the same transcript, and comparisons between versions are meaningful.
- Scenario authors must state what good looks like. This is more work than asking a model, and it is also the value of the product.
- Lexical similarity is blind to synonyms, which is why it can never fail a scenario on its own.

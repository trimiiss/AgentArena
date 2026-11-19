# 0008. Agents are reached over HTTP only, behind SSRF defences

Status: accepted

## Context

AgentArena calls URLs chosen by its customers from inside its own network. A hostile URL could reach cloud metadata services or internal APIs. Running customer code would be far riskier still.

## Decision

Agents are external HTTP services that implement a small, versioned protocol. AgentArena never executes customer code. Tools are simulated from the scenario, which also makes runs repeatable.

Calling a customer URL is defended in layers:

1. Only `http` and `https` are allowed, with no credentials in the URL.
2. The host must resolve to a public address, unless it is on an explicit allowlist. This runs at registration and again before every call, since DNS can change.
3. Redirects are not followed, and responses are capped at 1 MB.
4. In Kubernetes, the egress NetworkPolicy of workers excludes all private ranges and the metadata range. Only the sample agent is reachable inside the cluster.

## Alternatives

- **Run agents in sandboxed containers.** Would allow uploading code, but needs a separately designed isolation layer. Deferred until there is a real need.
- **Check the URL only at registration.** Defeated by changing DNS after approval.
- **An egress proxy that resolves and connects in one step.** The strongest fix for DNS rebinding, and the next step if the product hosts untrusted tenants at scale.

## Consequences

- Any framework can integrate with one endpoint, and the sample agent shows this in about 300 lines.
- A small DNS rebinding window remains between the check and the connection. The NetworkPolicy closes it in Kubernetes.
- Compose has no egress policy, so local development relies on the application check alone.
- Agents behind a private network cannot be tested without being exposed or allow-listed.

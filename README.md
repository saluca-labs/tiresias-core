# Tiresias

**Agent security infrastructure — identity, audit, policy, and observability for AI agents.**

> Who's watching your AI agents?

## The Problem

AI agents are deploying into production with no persistent identity, no audit trail, no policy enforcement, and no anomaly detection. The same governance gaps that enterprises solved for microservices a decade ago — service accounts, RBAC, distributed tracing, mTLS — are wide open in the agent layer.

## What Tiresias Does

Tiresias is the identity and authorization layer for autonomous AI systems. It sits between the agent framework and production, enforcing governance without limiting capability.

```
Agent Framework → [ Tiresias: Identity | Policy | Audit | Detection ] → Production
```

**Self-hosted by default. Model-agnostic. Source-available core (FSL-1.1-Apache, converts to Apache 2.0 after two years).**

## Core Capabilities

| Capability | What It Does |
|-----------|-------------|
| **Soulkeys** | Persistent cryptographic identity for AI agents. SHA-512, tenant-scoped, revocable. Every action logged against agent identity. |
| **Immutable Audit** | Tamper-evident hash-chain log. Each entry references the previous hash. Multi-replica safe. 15+ event types. |
| **Aletheia (CoT Auditing)** | Chain-of-thought capture with integrity guarantees. Encrypted at rest. Policy enforcement on the reasoning itself — inject/reject/warn. |
| **Policy Engine (PDP/PEP)** | YAML policy definitions, git-managed. Capability tokens (JWT). Tool policy engine controls what agents can invoke and when. |
| **Anomaly Detection** | 18 detection types. Behavioral baselines per agent. Real-time alerting. SIEM integration (Splunk, Elastic, Syslog, Sentinel). |
| **Observability Proxy** | Full request/response logging with envelope encryption. Token cost tracking. Multi-provider routing with automatic failover. |
| **Cost Attribution** | Per-request, per-agent, per-customer token tracking and cost breakdown across providers. |

## Quickstart

```bash
git clone https://github.com/saluca-labs/tiresias-core.git
cd tiresias-core
cp .env.example .env   # set TIRESIAS_TENANT_ID and your API keys
docker compose up -d
# Proxy: http://localhost:8080  Dashboard: http://localhost:3000
```

Point your OpenAI SDK at `http://localhost:8080/v1` — no code changes required.

## Why Tiresias

| | Tiresias | LangSmith | Helicone | Langfuse | Datadog LLM | Portkey |
|---|----------|-----------|----------|----------|-------------|---------|
| Self-hosted | **Yes** | No | Partial | Yes | No | No |
| Agent identity (soulkeys) | **Yes** | No | No | No | No | No |
| Hash-chain audit integrity | **Yes** | No | No | No | No | No |
| CoT auditing + enforcement | **Yes** | No | No | No | No | No |
| Policy engine (PDP/PEP) | **Yes** | No | No | No | No | No |
| Anomaly detection | **Yes** | No | No | No | No | No |
| Envelope encryption (BYOK) | **Yes** (ent) | No | No | No | No | No |
| Multi-provider failover | **Yes** | No | No | No | No | Yes |
| Non-LLM API analytics | **Yes** | No | No | No | No | No |
| Air-gap support | **Yes** (ent) | No | No | No | No | No |
| Core licence | FSL 1.1 -> Apache 2.0 | MIT | Apache 2.0 | MIT | Closed | Closed |

## MASP: Managed Agent Security Protection

What MSSP was to networks, MASP is to agents.

Enterprises are deploying AI agents at scale through platforms like Gumloop, Zapier, Make, CrewAI, and LangChain. None of them have built-in governance infrastructure. Compliance frameworks (SOC 2, EU AI Act, NIST AI RMF, ISO 42001, FedRAMP) now require audit trails, observability, and human oversight for AI systems.

Tiresias is the MASP layer — drop it into any agent platform to get identity, audit, policy, detection, and cost attribution without building it yourself.

## Module Structure

| Module | License | Description |
|--------|---------|-------------|
| `tiresias-core` | FSL-1.1-Apache | Proxy, dashboard, identity, audit, policy, analytics, multi-provider routing |
| `tiresias-enterprise` | Commercial | BYOK encryption, Aletheia CoT auditing, air-gap license relay, SIEM integration, MSSP/white-label |

## Learn More

- [Zero Trust for AI Agents](https://www.saluca.com/p/zero-trust-for-ai-agents) — why agents need their own security model
- [Your AI Agent Forgets Everything](https://www.saluca.com/p/your-ai-agent-forgets-everything) — the case for persistent agent identity
- [We've Been Chasing the Wrong Intelligence](https://www.saluca.com/p/weve-been-chasing-the-wrong-intelligence) — governance over capability

## Enterprise

**tiresias.network** — multi-tenant platform, MSSP support, portal, SIEM integrations, white-label.

Contact enterprise@saluca.com for enterprise licensing, BYOK, air-gap deployments, and partner programs.

---

*Built by [Saluca LLC](https://saluca.com). Source-available core under FSL-1.1-Apache. Enterprise tier available.*

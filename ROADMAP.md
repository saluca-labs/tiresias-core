# Tiresias Roadmap

> **Note, 2026-10-22:** the "Shipped" list below describes the Tiresias product, not this repository.
> Checked against `main` at 3c634b6, this repository does not contain the observer sidecar, the incident
> controller, the Grafana SOC stack, licence enforcement, or BYOK key providers other than local (the
> others raise "requires Tiresias Enterprise"); its pricing table has 30 model entries, not 52; per-tier
> rate limits (`proxy/rate_limit.py`) are the only tiering in core. The list is kept unchanged below.

## Shipped

### v1.0.0 — Core Proxy
- OpenAI-compatible endpoint (`/v1/chat/completions`)
- Multi-provider routing with cascade failover (OpenAI, Anthropic, Gemini, Groq)
- Streaming and non-streaming support
- Token cost tracking (52 models)
- Encrypted audit log (AEAD-AES-256, envelope encryption)
- BYOK encryption (Local, AWS KMS, HashiCorp Vault, Azure KV, GCP SM)
- Analytics dashboard (port 3000)
- License enforcement (Free/Starter/Pro/Enterprise)
- Session tagging via `x-tiresias-session-id`
- Generic API proxy (Phase 5 APIP)
- Observer sidecar (cost/volume/PII enforcement)
- Incident controller with playbook-driven response
- Grafana SOC stack (LGTM)
- Continuous pentest program

---

## Planned

### Workflow State Tracking (Enterprise)
**Priority:** High
**Origin:** Analysis of Anthropic Claude Code architecture leak (Nate B Jones, 2026-04-03). Claude Code's 12 agent primitives include "workflow state vs. conversation state" — Tiresias covers conversation state but not workflow state.

**Problem:** Enterprise agents run multi-step workflows (onboarding, incident response, compliance reviews, data pipelines). Tiresias sees every turn but has no concept of workflow boundaries, step progression, or completion state. When an agent stalls at step 3 of 5, no one knows until someone manually checks.

**Proposed capabilities:**
- **Workflow detection:** Infer workflow boundaries from turn patterns (session tags, tool call sequences, temporal gaps). Customers can also declare workflows explicitly via header (`x-tiresias-workflow-id`, `x-tiresias-workflow-step`).
- **Step tracking:** Track progression through multi-step workflows. Visualize completion state on the dashboard.
- **Stall alerting:** Detect when a workflow hasn't progressed within a configurable window. Alert via existing channels (Slack, Telegram, PagerDuty).
- **Workflow analytics:** Cost-per-workflow, time-per-step, completion rates, failure-point heatmaps. "Your compliance review workflow costs $12.40 on average and fails at step 3 in 18% of runs."
- **Workflow replay:** Reconstruct the full turn sequence for a completed or failed workflow. Feeds into incident controller for RCA.

**Why Tiresias (not the agent framework):**
- Framework-agnostic. Works with LangChain, CrewAI, custom agents — any framework that routes through the proxy.
- Fleet-wide view. Cross-agent workflows (Agent A hands off to Agent B) are visible at the proxy layer but invisible to either agent's framework.
- Compliance artifact. Workflow audit trails with encrypted payloads and hash integrity meet SOC 2 / ISO 27001 requirements.

**Design constraint:** Tiresias must remain a passive observer by default. Workflow tracking is opt-in (via headers or pattern configuration). The proxy never modifies, delays, or rejects requests based on workflow state — it only observes and alerts.

**Estimated scope:** 2-3 sessions design, 2-3 sessions build.

---

### Content Analyzer — CoT Audit Tooling (Enterprise, requires Aletheia CoT)
**Priority:** Medium
**Origin:** Claude Code architecture analysis + enterprise auditability requirements for regulated industries (legal, healthcare, financial services).
**Prerequisite:** Aletheia CoT capture must be enabled on the account. Available as a percentage-based add-on (e.g. 10% above current plan cost) to ANY tier that has CoT enabled — Starter, Pro, or Enterprise. No tier upgrade required.

**Privacy model:** Two-layer architecture. The proxy captures only structural metadata (counts, token sizes, hashed identifiers). The content analyzer is a customer-side open-source tool that runs on the customer's own decrypted data, inside their perimeter. Tiresias never inspects content.

**Layer 1 — Proxy (structural metadata, no content inspection):**
- `tools_loaded_count` — count of elements in the `tools` array (structural parse, no content read)
- `tool_definitions_tokens` — token count of the tools block (already metered)
- `tool_calls_count` — count of `tool_use` blocks in response (structural)
- `tool_call_hashes` — SHA-256 of tool names (hashed, never plaintext)

**Layer 2 — Customer-side analyzer (open-source CLI / Grafana plugin):**
Runs on the customer's own decrypted audit trail and CoT records. Tiresias never sees this data.

- **Tool usage forensics:** Map hashed tool identifiers back to names, compute loaded-vs-called ratios, identify idle tools.
- **CoT reasoning audit:** Parse thinking blocks to reconstruct the chain of reasoning — which tools were considered, why one was chosen over another, where the model hesitated or changed approach.
- **Compaction recommendations:** "Agent X loads 23 tools (4,200 tokens/turn) but uses 4. Removing 17 idle tools saves $1.02/session."
- **Tool drift detection:** Alert when tool usage patterns change significantly across sessions.
- **Compliance report generation:** Structured PDF/JSON reports for regulators showing what the AI did, what it considered, and why — reconstructed from CoT + audit trail.

**Pricing model:** Flat percentage add-on (target: ~10%) on top of the customer's existing plan cost. No tier change, no SKU complexity. If you have CoT, you can add the analyzer. The percentage covers the customer-side tooling, report templates, Grafana plugin, and support for compliance report generation. The open-source CLI is free; the managed dashboards and compliance templates are the paid layer.

**Estimated scope:** 1-2 sessions for proxy metadata capture, 2-3 sessions for the customer-side analyzer CLI.

---

### Output Verification Layer (Enterprise)
**Priority:** Medium
**Origin:** Claude Code primitive 8 (verification). Tiresias verifies integrity (SHA-256 hashes) but not correctness.

**Proposed capabilities:**
- **Policy compliance checking:** Verify that agent outputs conform to customer-defined content policies before forwarding to the end user. Configurable via policy YAML.
- **Hallucination flagging:** Lightweight classifier on the proxy that flags responses with low grounding confidence. Not blocking — adds a confidence header (`x-tiresias-confidence`) for the calling application to act on.
- **PII in output:** Extend existing PII detection from input-only to input+output.

**Design constraint:** Verification adds latency. Must be optional, async where possible, and never exceed 200ms for synchronous checks.

---

### Latency-Aware Routing (Pro+)
**Priority:** Low
**Origin:** Current cascade routing uses fixed priority order. At scale, customers want latency-optimized routing.

**Proposed capabilities:**
- Route to the fastest healthy provider, not just the first in the cascade.
- Weighted routing based on observed p50 latency per provider per model.
- Geographic affinity (prefer provider with lowest RTT from customer's region).

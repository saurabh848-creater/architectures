# Architecture Reference Library

**Saurabh Sarkar** — Solution Architect | AI Architecture | Financial Services

13 years in financial services. Currently leading architecture for AI-driven platform modernisation, KYC/AML transformation, and agentic AI systems at Tier 1 Private Banking and Financial Crime clients across the UK.

This repo contains architecture reference documents and working code skeletons from projects I have designed and delivered (or designed as proof-of-concept reference architectures). Each document includes problem framing, architecture diagrams, component deep-dives, Architecture Decision Records with explicit trade-offs, and regulatory/governance considerations.

I write these up because the decisions matter more than the diagrams. Anyone can draw boxes and arrows. The interesting work is in why you chose this over that, and what you had to give up.

---

## Architectures

| # | Document | Type | Domain | Status |
|---|----------|------|--------|--------|
| 01 | [iCLM — Intelligent Client Lifecycle Management](./01-iCLM-agentic-onboarding.md) | Agentic AI / Multi-Agent Orchestration | Private Banking Onboarding | Proof-of-Concept (Production-Representative Design) |
| 02 | [AI-Powered Unified AML Screening Platform](./02-aml-hybrid-detection.md) | Hybrid AI / ML / Graph Analytics | Financial Crime / AML | Delivered — Production |
| 03 | [Perpetual KYC — Event-Driven CLM Platform](./03-pkyc-event-driven-platform.md) | Event-Driven / Microservices / DDD | KYC / Client Lifecycle Management | Delivered — Production (£5M Programme) |

---

## Code Skeletons

Working LangGraph and Python skeletons for each architecture. These are not production deployments — they are structured starting points that reflect the real design decisions documented above.

| # | File | Architecture |
|---|------|-------------|
| S01 | [iclm_agent_graph.py](./skeletons/iclm_agent_graph.py) | iCLM multi-agent onboarding orchestration |
| S02 | [aml_detection_pipeline.py](./skeletons/aml_detection_pipeline.py) | AML hybrid detection: rules + ML + LLM disambiguation |
| S03 | [pkyc_event_processor.py](./skeletons/pkyc_event_processor.py) | pKYC event-driven risk delta processing |

---

## Architecture Principles I Work By

**Decisions over diagrams.** The diagram is a communication tool. The ADR is the actual architecture work. If I cannot articulate why I made a choice and what I gave up, the choice was not thought through.

**Governance is a first-class design concern in financial services.** You cannot bolt on explainability, audit trails, or regulatory compliance after the fact. They are structural constraints that shape every layer from the data model to the agent orchestration pattern.

**Honest about what is prototype and what is production.** I flag clearly where a design is a proof-of-concept and what the delta to production readiness would be. Overclaiming is how you fail the first technical interview question.

**Hybrid over single-model for regulated AI.** Rules handle deterministic regulatory requirements. ML handles statistical pattern detection. LLMs handle semantic and contextual reasoning. Each layer is independently auditable and replaceable. That matters in an FCA-regulated environment.

---

## Domain Background

My work sits at the intersection of agentic AI engineering and financial services architecture. The domains I work in most deeply:

- **KYC/AML and Financial Crime** — perpetual KYC, AML screening, SAR filing workflows, graph-based network analysis
- **Private Banking and Wealth Management** — Avaloq integrations, client lifecycle management, onboarding automation
- **AI Governance in Regulated Environments** — FCA compliance, EU AI Act, model governance frameworks, explainability

---

## Connect

- LinkedIn: [linkedin.com/in/sarkarsaurabh](https://linkedin.com/in/sarkarsaurabh)
- Location: London, UK

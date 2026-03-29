# Architecture Reference: iCLM — Intelligent Client Lifecycle Management

> **Domain:** Agentic AI / Private Banking Onboarding
> **Classification:** Architecture Proof-of-Concept (Production-Representative Design)
> **Author:** Saurabh Sarkar, Solution Architect
> **Last Updated:** 2025
> **Status:** Active Development

---

## 1. Problem Statement

Private banking client onboarding is one of the most document-intensive, compliance-heavy, and manually driven processes in financial services. At most Tier 1 PBWM institutions running Avaloq or Temenos, onboarding a single private banking client involves:

- 40 to 80 distinct data points collected across multiple touchpoints
- Manual review of identity documents, source of wealth evidence, and tax declarations
- Sequential handoffs between relationship managers, compliance officers, and operations teams
- Average cycle time of 14 to 28 days for a standard onboarding
- Regulatory obligations under FCA, MiFID II, GDPR, CRS/FATCA, and AML/KYC frameworks

The core architectural challenge is not automating individual steps. It is **orchestrating a set of intelligent, semi-autonomous agents that can reason, route, and escalate** across a complex, regulated workflow — while maintaining a full, auditable decision trail.

iCLM addresses this through a **multi-agent orchestration architecture** built on LangGraph, with LLM-powered decision nodes, human-in-the-loop governance checkpoints, and RAG-based knowledge retrieval.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        iCLM: SYSTEM ARCHITECTURE                            │
│                                                                             │
│  CLIENT CHANNEL                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Web Portal  |  RM Tablet App  |  API (3rd Party RM Tools)           │  │
│  └─────────────────────────┬────────────────────────────────────────────┘  │
│                             │ HTTPS / REST                                  │
│  ORCHESTRATION LAYER        ▼                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    FastAPI Gateway                                    │  │
│  │           Auth (OAuth2/OIDC)  |  Rate Limiting  |  Audit Log         │  │
│  └─────────────────────────┬────────────────────────────────────────────┘  │
│                             │                                               │
│  ┌──────────────────────────▼────────────────────────────────────────────┐ │
│  │                  LangGraph Agent Orchestrator                          │ │
│  │                                                                        │ │
│  │   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐                │ │
│  │   │  Document   │   │    KYC /    │   │   Risk &    │                │ │
│  │   │  Intake     │──▶│    AML      │──▶│  Suitability│                │ │
│  │   │  Agent      │   │   Agent     │   │   Agent     │                │ │
│  │   └─────────────┘   └─────────────┘   └──────┬──────┘                │ │
│  │          │                 │                   │                       │ │
│  │          ▼                 ▼                   ▼                       │ │
│  │   ┌─────────────────────────────────────────────────┐                 │ │
│  │   │           HUMAN-IN-THE-LOOP GATEWAY              │                 │ │
│  │   │   Compliance Officer Review  |  RM Approval      │                 │ │
│  │   │   Escalation Queue  |  Override Capture          │                 │ │
│  │   └─────────────────────────────────────────────────┘                 │ │
│  │                             │                                          │ │
│  │                             ▼                                          │ │
│  │   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐                │ │
│  │   │  Account    │   │  Regulatory │   │  Notification│               │ │
│  │   │  Setup      │──▶│  Reporting  │──▶│   Agent     │                │ │
│  │   │  Agent      │   │   Agent     │   │             │                │ │
│  │   └─────────────┘   └─────────────┘   └─────────────┘                │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  KNOWLEDGE & INTELLIGENCE LAYER                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  RAG Pipeline                                                         │  │
│  │  LlamaIndex  ──▶  Pinecone  ──▶  OpenAI Embeddings        │  │
│  │  (Policy Docs, Regulatory Guides, Product Rules, Country Risk)        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  DATA & INTEGRATION LAYER                                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │
│  │  Avaloq     │  │  World-Check│  │  PostgreSQL  │  │    GCP      │      │
│  │  Core       │  │  / Factiva  │  │  State Store │  │  Pub/Sub    │      │
│  │  Banking    │  │  (Screening)│  │              │  │  (Events)   │      │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Agent Topology

Each agent in the LangGraph graph is a stateful node with defined inputs, outputs, tool access, and escalation conditions.

### 3.1 Document Intake Agent

**Responsibility:** Receive, classify, and extract structured data from onboarding documents (passport, utility bill, source of wealth declaration, tax forms).

**Tools available:**
- `extract_document_fields` — OCR + NLP extraction via Azure Document Intelligence
- `classify_document_type` — LLM-based classification with confidence scoring
- `validate_expiry_and_authenticity` — rules-based validation layer
- `flag_for_manual_review` — escalation tool with reason capture

**Escalation condition:** Confidence score below 0.85 on any mandatory field, or document type unrecognised.

### 3.2 KYC / AML Agent

**Responsibility:** Screen client identity and beneficial ownership against sanctions lists, PEP databases, and adverse media. Assess AML risk classification.

**Tools available:**
- `screen_world_check` — World-Check One API integration
- `screen_factiva` — adverse media pipeline
- `resolve_entity` — LLM-based entity disambiguation for name/alias matching
- `calculate_aml_risk_score` — rules + ML composite scoring
- `request_enhanced_due_diligence` — triggers EDD workflow for high-risk clients

**Escalation condition:** PEP match, sanctions hit, composite AML risk score above threshold, or ambiguous entity resolution requiring human judgement.

### 3.3 Risk and Suitability Agent

**Responsibility:** Assess client suitability for product categories based on MiFID II obligations. Generate investment risk profile.

**Tools available:**
- `retrieve_suitability_policy` — RAG lookup against current MiFID II suitability policy documents
- `classify_risk_profile` — LLM-based classification with policy-grounded reasoning
- `generate_suitability_rationale` — produces explainable, regulator-ready rationale text
- `flag_unsuitable_products` — gates product offerings based on profile

### 3.4 Human-in-the-Loop Gateway

This is not an agent. It is a **stateful pause node** in the LangGraph graph that suspends execution and routes to a human review queue when any upstream agent raises an escalation flag.

**Design principle:** The system never makes a final onboarding decision autonomously for high-risk or ambiguous cases. Human override is always captured with reason, timestamp, and approver identity.

### 3.5 Account Setup Agent

**Responsibility:** Trigger downstream account provisioning in Avaloq once all checks pass and human approvals are captured.

**Tools available:**
- `create_avaloq_client_record` — Avaloq WAPI integration
- `provision_account_structure` — sets up account hierarchy per product selection
- `trigger_crs_fatca_reporting` — initiates regulatory reporting obligations

---

## 4. RAG Pipeline Design

```
┌──────────────────────────────────────────────────────────┐
│                  RAG PIPELINE ARCHITECTURE                │
│                                                          │
│  INGESTION                                               │
│  Policy PDFs ──┐                                         │
│  Reg Guides  ──┼──▶  LlamaIndex Parser                   │
│  Country Risk ─┘     (Chunking: 512 tokens, 50 overlap)  │
│                              │                           │
│                              ▼                           │
│                    OpenAI text-embedding-3-small          │
│                              │                           │
│                              ▼                           │
│                    Pinecone Vector Store                  │
│                    (Namespace per document type)          │
│                                                          │
│  RETRIEVAL (per agent query)                             │
│  Agent Query ──▶  Hybrid Search (dense + BM25)           │
│                              │                           │
│                              ▼                           │
│                    Re-ranking (cross-encoder)             │
│                              │                           │
│                              ▼                           │
│                    Top-k Chunks (k=5) ──▶ LLM Context    │
└──────────────────────────────────────────────────────────┘
```

**Key design decision:** Hybrid search (dense vector + BM25 sparse) was chosen over pure dense retrieval because regulatory policy documents contain highly specific terminology, abbreviations, and article references that sparse keyword matching handles more reliably than semantic embedding alone.

---

## 5. Architecture Decision Records

### ADR-001: LangGraph over CrewAI for Agent Orchestration

**Context:** Multiple agent orchestration frameworks evaluated including CrewAI, AutoGen, and LangGraph.

**Decision:** LangGraph.

**Rationale:**
- LangGraph exposes the graph topology explicitly, making it auditable and inspectable — a hard requirement in regulated financial services where the orchestration logic itself may be subject to regulatory review.
- CrewAI abstracts away the control flow, making it difficult to insert deterministic governance checkpoints.
- LangGraph's stateful graph model maps naturally to the onboarding workflow's stage-gated process.
- Human-in-the-loop interruption is a first-class primitive in LangGraph, not a workaround.

**Trade-offs accepted:** More boilerplate code than CrewAI. Higher initial development complexity.

### ADR-002: RAG over Fine-Tuning for Policy Knowledge Retrieval

**Context:** Needed agents to reason over frequently updated regulatory policy documents.

**Decision:** RAG with LlamaIndex and Pinecone.

**Rationale:**
- Regulatory documents change frequently (FCA policy updates, MiFID II amendments). Fine-tuned models cannot be updated without retraining cycles.
- RAG provides a clear audit trail — retrieved chunks can be logged alongside model outputs, supporting explainability requirements.
- Fine-tuning requires labelled data that is difficult to produce in a regulated context.

### ADR-003: Stateful Pause Node over Async Callback for Human Review

**Context:** Human review integration point needed to handle compliance officer approval without losing workflow state.

**Decision:** LangGraph interrupt-and-resume with persistent PostgreSQL state store.

**Rationale:** Async callbacks require the external system to hold state or re-POST full context. The LangGraph interrupt pattern serialises the full graph state to a persistent store, allowing resumption from any point without data loss — including across service restarts.

---

## 6. Regulatory and Governance Considerations

| Requirement | Design Response |
|---|---|
| GDPR Article 22 — automated decision-making | Human-in-the-loop mandatory for all final onboarding decisions |
| MiFID II suitability assessment | RAG-grounded rationale generation with policy citation |
| AML / FCA | Composite risk scoring with full audit trail |
| CRS / FATCA | Automated reporting agent with structured output validation |
| EU AI Act — High Risk AI classification | Model cards, drift monitoring, human oversight, audit logs |
| Data residency | All processing within GCP europe-west2 region |

---

## 7. Tech Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | LangGraph (Python) |
| LLM | OpenAI GPT-4o via Azure OpenAI Service |
| RAG Framework | LlamaIndex |
| Vector Store | Pinecone (production) / ChromaDB (local dev) |
| Embeddings | OpenAI text-embedding-3-small |
| API Gateway | FastAPI + Pydantic |
| State Persistence | PostgreSQL (LangGraph checkpointer) |
| Event Streaming | GCP Pub/Sub |
| Banking Integration | Avaloq WAPI / CAPI |
| Screening | World-Check One API, Factiva |
| Infrastructure | GCP (Cloud Run, GKE, Secret Manager, IAM) |
| Observability | LangSmith (tracing), Cloud Monitoring |

---

## 8. Limitations and Future State

**Current limitations:**
- The prototype uses synthetic onboarding data. Production deployment would require Avaloq tenant integration and live screening API credentials.
- Multi-language document processing (Arabic, Chinese) is not yet implemented in the extraction pipeline.
- The risk scoring model is a heuristic composite, not a trained ML model. A production system would require model validation under SR 11-7 equivalent standards.

**Next evolution:**
- Fine-grained tool-use evaluation framework for each agent
- Multi-modal document intelligence for handwritten source-of-wealth declarations
- Integration with a dedicated AML graph database (Neo4j) for network-level risk propagation across the client book

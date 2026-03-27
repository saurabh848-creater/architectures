# Architecture Reference: Perpetual KYC (pKYC) — Event-Driven CLM Platform

> **Domain:** KYC / Client Lifecycle Management
> **Engagement Type:** Client Programme (Tier 1 Bank) — £5M Transformation
> **Author:** Saurabh Laturkar, Solution Architect
> **Last Updated:** 2025
> **Status:** Delivered — Production

---

## 1. Problem Statement

Traditional KYC refresh models operate on fixed periodic cycles — typically every 1, 3, or 5 years depending on client risk classification. This creates three compounding risks:

**Regulatory risk:** A client's risk profile can change materially between review cycles (new adverse media, change of jurisdiction, PEP appointment, ownership structure change). The bank remains unaware until the next scheduled review.

**Client experience risk:** Periodic KYC reviews generate friction-heavy, document-intensive client interactions — often perceived as intrusive and disconnected from any real business trigger. For a private bank serving UHNW clients, this is a relationship risk.

**Operational cost risk:** Large periodic review cycles require significant compliance headcount to be deployed simultaneously on bulk caseloads, creating inefficiency and inconsistent quality.

**Perpetual KYC (pKYC)** is the architectural response: a continuous monitoring model where the client record is kept current through real-time event ingestion and targeted, trigger-based reviews — rather than scheduled bulk refreshes.

This architecture document describes the £5M transformation programme that shifted a Tier 1 bank from a periodic to a perpetual KYC model, delivering £1.2M in annual compliance savings.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│             PERPETUAL KYC: EVENT-DRIVEN CLM PLATFORM                        │
│                                                                              │
│  EVENT SOURCES (What triggers a pKYC review?)                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ Factiva  │ │World-    │ │ Internal │ │ Client   │ │ Scheduled│        │
│  │ Adverse  │ │Check PEP │ │ System   │ │ Self-    │ │ Trigger  │        │
│  │ Media    │ │ /Sanction│ │ Events   │ │ Declared │ │ (Risk    │        │
│  │ Feed     │ │ Alerts   │ │(acct     │ │ Changes  │ │ Expiry)  │        │
│  │          │ │          │ │ changes) │ │          │ │          │        │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘        │
│       └────────────┴────────────┴────────────┴────────────┘                │
│                                    │                                        │
│  EVENT STREAMING LAYER             ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    Azure Event Grid                                   │  │
│  │   Schema validation  |  Event routing  |  Dead letter queue          │  │
│  │   Idempotency keys   |  Guaranteed delivery  |  Replay capability    │  │
│  └──────────────────────────────┬───────────────────────────────────────┘  │
│                                  │                                          │
│  ORCHESTRATION LAYER             ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    pKYC Event Processor (AKS)                         │  │
│  │                                                                       │  │
│  │  Event Classification Engine                                          │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │  │
│  │  │  Event Type?  ──▶  Risk Delta Calculation  ──▶  Review Trigger? │ │  │
│  │  │                                                                  │ │  │
│  │  │  Low delta  ──▶  Auto-update client record (no review)          │ │  │
│  │  │  Medium delta ──▶  Targeted document request (self-serve)       │ │  │
│  │  │  High delta  ──▶  Full compliance officer review                │ │  │
│  │  │  Sanctions hit ──▶  Immediate escalation + account restriction  │ │  │
│  │  └─────────────────────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                  │                                          │
│  CLIENT ENGAGEMENT LAYER         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Digital Journey (AngularJS + AEM)                                    │  │
│  │                                                                       │  │
│  │  Self-Serve Portal  ──▶  Targeted document upload                    │  │
│  │  RM Dashboard       ──▶  Client review task queue                    │  │
│  │  CIAM (Ping Identity) ──▶  Step-up authentication for sensitive ops  │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                  │                                          │
│  CASE MANAGEMENT LAYER           ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Modernised Pega CLM Platform                                         │  │
│  │                                                                       │  │
│  │  Case creation  |  SLA tracking  |  Escalation routing               │  │
│  │  Four-eyes approval  |  Override capture  |  Audit trail             │  │
│  │  Regulatory reporting triggers (STR / SAR / CRS / FATCA)            │  │
│  └──────────────────────────────┬────────────────────────────────────────┘  │
│                                  │                                          │
│  DATA LAYER                      ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Azure SQL (Client Master)  |  Azure Blob (Document Store)           │  │
│  │  Event Store (append-only, immutable)  |  Data Lake (analytics)      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  SCREENING PIPELINES                                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  NetReveal (PEP / Sanctions)  |  Factiva (Adverse Media)             │  │
│  │  Continuous background screening — not batch                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Domain Model (DDD Bounded Contexts)

This architecture was designed using Domain-Driven Design. The key bounded contexts and their relationships are:

```
┌─────────────────────────────────────────────────────────────────┐
│                    BOUNDED CONTEXTS                              │
│                                                                  │
│  ┌─────────────────┐         ┌─────────────────┐               │
│  │  CLIENT IDENTITY │         │  RISK PROFILE   │               │
│  │  CONTEXT         │         │  CONTEXT        │               │
│  │                  │◀──────▶│                  │               │
│  │  Client Master   │  ACL   │  AML Risk Score  │               │
│  │  Document Store  │        │  Suitability     │               │
│  │  KYC Status      │        │  Classification  │               │
│  └──────────────────┘         └─────────────────┘               │
│           │                            │                         │
│           │ Domain Events              │ Domain Events           │
│           ▼                            ▼                         │
│  ┌─────────────────┐         ┌─────────────────┐               │
│  │  ONBOARDING     │         │  SCREENING      │               │
│  │  CONTEXT        │         │  CONTEXT        │               │
│  │                  │         │                  │               │
│  │  CLM Workflow   │         │  PEP / Sanctions │               │
│  │  Case Mgmt      │         │  Adverse Media   │               │
│  │  RM Tasks       │         │  Continuous Mon. │               │
│  └──────────────────┘         └─────────────────┘               │
│                                                                  │
│  Integration pattern: Domain events via Azure Event Grid         │
│  Anti-corruption layers (ACL) at all bounded context boundaries  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Event Schema Design

```
EVENT: ClientRiskDeltaDetected

{
  "eventId": "uuid-v4",
  "eventType": "kyc.risk.delta.detected",
  "eventVersion": "1.0",
  "timestamp": "2025-03-15T09:23:11Z",
  "source": "screening.factiva.adverse-media",

  "payload": {
    "clientId": "CLT-00123456",
    "clientSegment": "UHNW",
    "currentRiskRating": "LOW",
    "detectedRiskSignal": {
      "type": "ADVERSE_MEDIA",
      "description": "Regulatory enforcement action in source country",
      "severity": "HIGH",
      "sourceUrl": "[redacted]",
      "confidence": 0.91
    },
    "recommendedAction": "COMPLIANCE_REVIEW",
    "slaHours": 24
  },

  "metadata": {
    "correlationId": "uuid-v4",
    "schemaVersion": "1.0",
    "producerService": "screening-service-v2.1"
  }
}
```

**Key design decisions in the event schema:**
- Events are **immutable facts**, not commands. The event describes what was detected, not what to do.
- `recommendedAction` is a signal, not a directive. The event processor makes the routing decision.
- Events are versioned from the outset to support schema evolution without breaking consumers.
- `correlationId` enables distributed tracing across the full pKYC processing chain.

---

## 5. Architecture Decision Records

### ADR-001: Event-Driven over Request-Response for pKYC State Transitions

**Context:** The team debated whether pKYC state transitions (risk changes, review triggers) should be handled via synchronous API calls or asynchronous event streams.

**Decision:** Event-driven via Azure Event Grid.

**Rationale:**
- KYC state changes are facts that occurred in the real world (a new adverse media article was published). They should be modelled as immutable domain events, not as API calls that may fail and need retry logic.
- Multiple downstream services need to react to the same risk event (case management, RM notification, regulatory reporting). Event-driven fan-out handles this naturally; synchronous API calls require orchestration complexity.
- Event replay capability is essential for regulatory audit scenarios where the bank must demonstrate exactly what information was available and when.
- Gregor Hohpe's canonical pattern here is clear: use events for things that happened, use commands for things you want to happen.

**Trade-offs accepted:** Eventual consistency. The client's risk status may take seconds to propagate across all downstream systems. This was accepted as adequate for the pKYC use case, where real-time consistency is not a hard requirement.

### ADR-002: Microservices over Modular Monolith for CLM Platform

**Context:** The legacy platform was a Pega monolith. Decision required on target architecture.

**Decision:** Microservices on AKS, with Pega retained for case management workflow only.

**Rationale:**
- The team applied Sam Newman's "strangler fig" decomposition pattern. Pega was not replaced wholesale; it was retained for the bounded context it handles best (case workflow, SLA tracking, four-eyes approval) and surrounded by purpose-built microservices for event processing, screening integration, and digital journeys.
- Deploying Pega as the entire CLM platform would have locked the bank into Pega's release cycle for all future capability enhancements, including AI augmentation.
- Independent deployability of the event processor, screening pipeline, and digital journey services was a hard requirement from the bank's DevOps practice.

### ADR-003: Append-Only Event Store over Mutable Client Record as System of Truth

**Context:** Where should the authoritative record of the client's KYC history reside?

**Decision:** Append-only event store as the system of truth; the client master in Azure SQL is a materialised projection.

**Rationale:**
- Regulatory requirement (FCA SYSC 3.2) requires the bank to demonstrate what it knew about a client and when. A mutable record cannot satisfy this — updating a field destroys the prior state.
- An append-only event store provides a complete, auditable history of every state change and its trigger.
- Martin Fowler's Event Sourcing pattern was applied here. The current client state is derived by replaying the event log.

**Trade-offs accepted:** Query complexity is higher against an event store than a mutable record. Materialised projections (read models) in Azure SQL handle this for operational queries.

---

## 6. Compliance Architecture

```
REGULATORY OBLIGATIONS AND DESIGN RESPONSES

FCA SYSC 3.2 — Record keeping
  Response: Append-only event store, immutable audit trail,
            7-year retention with automated lifecycle policy

GDPR Article 5(1)(e) — Storage limitation
  Response: Automated data minimisation pipeline
            Document expiry triggers post-review
            Right-to-erasure handled via pseudonymisation in event log

GDPR Article 25 — Data protection by design
  Response: CIAM integration for step-up auth on sensitive data access
            Field-level encryption on PII in Azure SQL
            Least-privilege IAM policy across all services

CSDR / CRS / FATCA — Regulatory reporting
  Response: Dedicated regulatory reporting microservice
            Structured output validation before submission
            Automated reconciliation checks

AML Directive (6AMLD) — Beneficial ownership
  Response: Explicit beneficial ownership data model
            Continuous ownership chain monitoring
            Threshold alerts at 25% ownership change
```

---

## 7. Outcomes

| Metric | Baseline (Periodic KYC) | Post-Implementation (pKYC) |
|---|---|---|
| Annual compliance cost | Baseline | £1.2M saving (24% reduction) |
| Average days to detect risk event | 365 to 1,825 days (cycle-dependent) | Less than 24 hours |
| False review rate (reviews with no material finding) | ~65% | ~31% |
| Client friction score (RM survey) | 3.1 / 5 | 4.2 / 5 |
| Regulatory finding rate (internal audit) | 3 findings per cycle | 0 findings post-implementation |

---

## 8. Tech Stack

| Layer | Technology |
|---|---|
| Event Streaming | Azure Event Grid |
| Microservices Runtime | Azure Kubernetes Service (AKS) |
| Case Management | Pega CLM (retained, decomposed) |
| Digital Journey | AngularJS, Adobe Experience Manager (AEM) |
| Identity and Auth | Ping Identity (CIAM), OAuth2/OIDC |
| Screening | Factiva (adverse media), NetReveal (PEP / sanctions) |
| Client Master | Azure SQL Server |
| Event Store | Azure Cosmos DB (append-only, change feed) |
| Document Store | Azure Blob Storage |
| Security | MS Defender, Azure Policy, field-level encryption |
| Automation | UiPath (legacy data migration, bulk processing) |
| Observability | Azure Monitor, Application Insights, Splunk |

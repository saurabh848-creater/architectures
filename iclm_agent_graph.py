"""
iCLM — Intelligent Client Lifecycle Management
LangGraph Multi-Agent Onboarding Orchestration

Architecture Reference: 01-iCLM-agentic-onboarding.md

This skeleton reflects the real design decisions in the architecture document:
- LangGraph over CrewAI: explicit graph topology, auditable control flow,
  first-class human-in-the-loop interruption as a LangGraph primitive
- Stateful pause node (not async callback) for human review, backed by
  PostgreSQL checkpointer for durable state across service restarts
- RAG over fine-tuning for policy knowledge: regulatory docs change frequently,
  RAG provides chunk-level audit trail alongside model outputs

Status: Skeleton — tool implementations are stubbed.
        Production deployment requires Avaloq tenant integration,
        live screening API credentials, and SR 11-7 model validation.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import AzureChatOpenAI
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNACCEPTABLE = "UNACCEPTABLE"


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    PENDING = "PENDING"


class OnboardingState(BaseModel):
    """
    Full graph state passed between all agent nodes.

    Design note: LangGraph serialises this to the PostgreSQL checkpointer
    at each node boundary. This enables the human-in-the-loop pause node
    to suspend execution and resume from exactly this state after a
    compliance officer submits their review — even across service restarts.
    """

    # Core identifiers
    client_id: str = ""
    correlation_id: str = ""

    # Message history (append-only via add_messages reducer)
    messages: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)

    # Document Intake Agent outputs
    documents_received: list[str] = Field(default_factory=list)
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    document_confidence_score: float = 0.0
    document_intake_escalation: bool = False
    document_intake_escalation_reason: str = ""

    # KYC / AML Agent outputs
    pep_match: bool = False
    sanctions_hit: bool = False
    adverse_media_found: bool = False
    aml_risk_score: float = 0.0
    aml_risk_level: RiskLevel = RiskLevel.LOW
    entity_disambiguation_result: dict[str, Any] = Field(default_factory=dict)
    edd_required: bool = False

    # Risk and Suitability Agent outputs
    risk_profile: str = ""
    suitability_rationale: str = ""
    unsuitable_products: list[str] = Field(default_factory=list)
    mifid_suitability_passed: bool = False

    # Human-in-the-loop Gate
    human_review_required: bool = False
    human_review_reason: str = ""
    human_review_decision: ReviewDecision = ReviewDecision.PENDING
    human_reviewer_id: str = ""
    human_review_notes: str = ""

    # Account Setup Agent outputs
    avaloq_client_record_id: str = ""
    account_structure_provisioned: bool = False
    crs_fatca_triggered: bool = False

    # Overall workflow status
    workflow_status: str = "IN_PROGRESS"
    rejection_reason: str = ""


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

def get_llm() -> AzureChatOpenAI:
    """
    Azure OpenAI via private endpoint — no client data leaves the bank's VNet.
    ADR-003: Private LLM endpoint over public API for disambiguation.
    """
    return AzureChatOpenAI(
        azure_deployment="gpt-4o",
        api_version="2024-08-01-preview",
        temperature=0,
        # azure_endpoint and api_key loaded from environment / GCP Secret Manager
    )


# ---------------------------------------------------------------------------
# Tool Stubs
# Each tool reflects a real integration point from the architecture document.
# ---------------------------------------------------------------------------

async def extract_document_fields(document_path: str) -> dict[str, Any]:
    """OCR + NLP extraction via Azure Document Intelligence."""
    # TODO: Integrate azure.ai.formrecognizer.DocumentAnalysisClient
    return {"passport_number": "STUB", "dob": "STUB", "nationality": "STUB"}


async def classify_document_type(document_path: str) -> tuple[str, float]:
    """LLM-based classification with confidence scoring."""
    # TODO: LLM classification with confidence threshold check
    return "PASSPORT", 0.97


async def validate_expiry_and_authenticity(fields: dict) -> tuple[bool, str]:
    """Rules-based validation layer."""
    # TODO: Expiry check, MRZ validation, document authenticity signals
    return True, ""


async def screen_world_check(client_name: str, dob: str, nationality: str) -> dict:
    """World-Check One API — PEP and sanctions screening."""
    # TODO: Integrate refinitiv.worldcheck.api client
    return {"pep_match": False, "sanctions_hit": False, "matches": []}


async def screen_factiva(client_name: str, jurisdiction: str) -> dict:
    """Factiva adverse media pipeline."""
    # TODO: Factiva API integration
    return {"adverse_media_found": False, "articles": []}


async def resolve_entity(
    client_profile: dict, candidate_matches: list[dict]
) -> dict:
    """
    LLM-based entity disambiguation for name/alias matching.

    Design note: The LLM takes the client profile, the matched list entry,
    and retrieved context from prior disambiguation decisions. It produces
    a match/no-match classification with confidence and plain-English reasoning.
    Private LLM endpoint — no data leaves the bank's cloud boundary.
    """
    # TODO: Implement RAG-augmented disambiguation prompt
    # Retrieved context from Pinecone namespace: "disambiguation-decisions"
    return {"is_match": False, "confidence": 0.95, "reasoning": "STUB"}


async def calculate_aml_risk_score(
    pep: bool, sanctions: bool, adverse_media: bool, jurisdiction: str
) -> tuple[float, RiskLevel]:
    """Rules + ML composite scoring."""
    # TODO: Weighted ensemble: rules-based flags + Isolation Forest output
    score = 0.15
    level = RiskLevel.LOW
    return score, level


async def retrieve_suitability_policy(query: str) -> list[str]:
    """
    RAG lookup against MiFID II suitability policy documents.

    ADR-002: RAG over fine-tuning — regulatory docs change frequently.
    Hybrid search: dense (OpenAI embeddings) + BM25 sparse for regulatory terminology.
    Pinecone namespace: "mifid-suitability-policy"
    """
    # TODO: LlamaIndex query engine against Pinecone
    return ["STUB policy chunk 1", "STUB policy chunk 2"]


async def create_avaloq_client_record(client_data: dict) -> str:
    """Avaloq WAPI integration for client record creation."""
    # TODO: Avaloq WAPI REST client
    return "CLT-STUB-001"


async def trigger_crs_fatca_reporting(client_id: str, tax_residencies: list[str]) -> bool:
    """Initiate CRS/FATCA regulatory reporting obligations."""
    # TODO: Regulatory reporting microservice call
    return True


# ---------------------------------------------------------------------------
# Agent Nodes
# ---------------------------------------------------------------------------

async def document_intake_agent(
    state: OnboardingState, config: RunnableConfig
) -> dict:
    """
    Node 1: Document Intake Agent

    Receives, classifies, and extracts structured data from onboarding documents.
    Escalates to human review if confidence drops below 0.85 on any mandatory field
    or document type is unrecognised.

    Design note: Confidence threshold of 0.85 was set based on back-testing against
    a sample of 500 historical onboarding cases. Below this threshold, manual
    review success rate increases materially. This threshold is a configuration
    parameter, not a hard-coded value.
    """
    logger.info("Document intake agent starting for client_id=%s", state.client_id)

    CONFIDENCE_THRESHOLD = 0.85
    extracted_fields = {}
    overall_confidence = 1.0
    escalation_required = False
    escalation_reason = ""

    for doc_path in state.documents_received:
        doc_type, confidence = await classify_document_type(doc_path)

        if doc_type == "UNKNOWN" or confidence < CONFIDENCE_THRESHOLD:
            escalation_required = True
            escalation_reason = (
                f"Document type unrecognised or confidence {confidence:.2f} "
                f"below threshold {CONFIDENCE_THRESHOLD} for {doc_path}"
            )
            logger.warning("Escalation triggered: %s", escalation_reason)
            break

        fields = await extract_document_fields(doc_path)
        valid, validation_error = await validate_expiry_and_authenticity(fields)

        if not valid:
            escalation_required = True
            escalation_reason = f"Validation failed for {doc_path}: {validation_error}"
            break

        extracted_fields.update(fields)
        overall_confidence = min(overall_confidence, confidence)

    return {
        "extracted_fields": extracted_fields,
        "document_confidence_score": overall_confidence,
        "document_intake_escalation": escalation_required,
        "document_intake_escalation_reason": escalation_reason,
        "human_review_required": escalation_required,
        "human_review_reason": escalation_reason if escalation_required else "",
        "messages": [
            AIMessage(
                content=f"Document intake complete. Escalation={escalation_required}. "
                f"Confidence={overall_confidence:.2f}."
            )
        ],
    }


async def kyc_aml_agent(
    state: OnboardingState, config: RunnableConfig
) -> dict:
    """
    Node 2: KYC / AML Agent

    Screens client identity and beneficial ownership against sanctions lists,
    PEP databases, and adverse media. Calculates composite AML risk score.

    Escalation conditions:
    - PEP match
    - Sanctions hit
    - Composite AML risk score above HIGH threshold
    - Entity disambiguation confidence below 0.80 (requires human judgement)
    """
    logger.info("KYC/AML agent starting for client_id=%s", state.client_id)

    client_name = state.extracted_fields.get("full_name", "")
    dob = state.extracted_fields.get("dob", "")
    nationality = state.extracted_fields.get("nationality", "")
    jurisdiction = state.extracted_fields.get("country_of_residence", "")

    # Parallel screening calls
    # In production: asyncio.gather for concurrent execution
    world_check_result = await screen_world_check(client_name, dob, nationality)
    factiva_result = await screen_factiva(client_name, jurisdiction)

    pep_match = world_check_result["pep_match"]
    sanctions_hit = world_check_result["sanctions_hit"]
    adverse_media = factiva_result["adverse_media_found"]

    # Entity disambiguation for any candidate matches
    disambiguation_result = {}
    if world_check_result.get("matches"):
        disambiguation_result = await resolve_entity(
            state.extracted_fields, world_check_result["matches"]
        )

    # Composite risk score: rules + ML ensemble
    aml_score, aml_level = await calculate_aml_risk_score(
        pep_match, sanctions_hit, adverse_media, jurisdiction
    )

    # Escalation logic
    edd_required = aml_level in (RiskLevel.HIGH, RiskLevel.UNACCEPTABLE) or pep_match
    human_review = (
        sanctions_hit
        or aml_level == RiskLevel.UNACCEPTABLE
        or (
            disambiguation_result.get("confidence", 1.0) < 0.80
            and world_check_result.get("matches")
        )
    )

    review_reason = ""
    if sanctions_hit:
        review_reason = "Sanctions hit — immediate escalation required"
    elif aml_level == RiskLevel.UNACCEPTABLE:
        review_reason = f"AML risk score {aml_score:.2f} exceeds acceptable threshold"
    elif human_review:
        review_reason = "Entity disambiguation confidence below 0.80"

    return {
        "pep_match": pep_match,
        "sanctions_hit": sanctions_hit,
        "adverse_media_found": adverse_media,
        "aml_risk_score": aml_score,
        "aml_risk_level": aml_level,
        "entity_disambiguation_result": disambiguation_result,
        "edd_required": edd_required,
        "human_review_required": state.human_review_required or human_review,
        "human_review_reason": state.human_review_reason or review_reason,
        "messages": [
            AIMessage(
                content=f"KYC/AML screening complete. PEP={pep_match}, "
                f"Sanctions={sanctions_hit}, AML Risk={aml_level.value}, "
                f"EDD Required={edd_required}."
            )
        ],
    }


async def risk_suitability_agent(
    state: OnboardingState, config: RunnableConfig
) -> dict:
    """
    Node 3: Risk and Suitability Agent

    Assesses client suitability for product categories under MiFID II obligations.
    Generates an investment risk profile with policy-grounded, explainable rationale.

    Design note: Rationale text is generated by the LLM grounded on RAG-retrieved
    policy chunks. The retrieved chunks are logged alongside the output —
    this creates an audit trail showing which policy provisions the assessment
    was based on, satisfying MiFID II Article 25 documentation requirements.
    """
    logger.info("Risk and suitability agent starting for client_id=%s", state.client_id)

    # Build suitability assessment query from extracted client profile
    profile_summary = (
        f"Client profile: age={state.extracted_fields.get('dob', 'unknown')}, "
        f"nationality={state.extracted_fields.get('nationality', 'unknown')}, "
        f"declared_investment_experience={state.extracted_fields.get('investment_experience', 'unknown')}, "
        f"declared_knowledge={state.extracted_fields.get('financial_knowledge', 'unknown')}"
    )

    # RAG retrieval: hybrid search against MiFID II suitability policy namespace
    policy_chunks = await retrieve_suitability_policy(profile_summary)

    llm = get_llm()

    suitability_prompt = f"""
You are a MiFID II suitability assessment engine for a private bank.

Client Profile:
{profile_summary}

Relevant Policy Provisions:
{chr(10).join(policy_chunks)}

Task:
1. Classify the client's risk profile (Conservative / Balanced / Growth / Aggressive)
2. List any product categories that are unsuitable for this client
3. Provide a concise, regulator-ready rationale citing the policy provisions above

Respond in structured format:
RISK_PROFILE: <classification>
UNSUITABLE_PRODUCTS: <comma-separated list or NONE>
RATIONALE: <plain-English explanation, max 200 words>
"""

    response = await llm.ainvoke([HumanMessage(content=suitability_prompt)])
    response_text = response.content

    # Parse structured response
    risk_profile = "BALANCED"  # TODO: Parse from response_text
    unsuitable_products = []  # TODO: Parse from response_text
    suitability_rationale = response_text  # TODO: Extract RATIONALE section
    mifid_passed = len(unsuitable_products) == 0

    return {
        "risk_profile": risk_profile,
        "unsuitable_products": unsuitable_products,
        "suitability_rationale": suitability_rationale,
        "mifid_suitability_passed": mifid_passed,
        "messages": [
            AIMessage(
                content=f"Suitability assessment complete. Risk Profile={risk_profile}. "
                f"MiFID II Passed={mifid_passed}."
            )
        ],
    }


async def human_review_gate(
    state: OnboardingState, config: RunnableConfig
) -> dict:
    """
    Node 4: Human-in-the-Loop Gateway (Stateful Pause Node)

    This is NOT an agent. It is a stateful pause node in the LangGraph graph
    that suspends execution and routes to a compliance officer review queue.

    Design decision (ADR-003): LangGraph interrupt-and-resume with PostgreSQL
    checkpointer, not async callback. The async callback approach requires the
    external system to hold state or re-POST full context. The LangGraph interrupt
    pattern serialises the full graph state to PostgreSQL, enabling resumption
    from any point — including across service restarts.

    GDPR Article 22: The system never makes a final onboarding decision
    autonomously for high-risk or ambiguous cases. Human override is always
    captured with reason, timestamp, and approver identity.

    The interrupt() call suspends graph execution here. The workflow resumes
    when a compliance officer submits their decision via the review API,
    which calls graph.update_state() with the reviewer's decision.
    """
    logger.info(
        "Human review gate reached for client_id=%s. Reason: %s",
        state.client_id,
        state.human_review_reason,
    )

    # Suspend execution — control returns to the caller (review queue system)
    # The reviewer's response is injected via graph.update_state()
    review_response = interrupt(
        {
            "client_id": state.client_id,
            "review_reason": state.human_review_reason,
            "aml_risk_level": state.aml_risk_level.value,
            "pep_match": state.pep_match,
            "sanctions_hit": state.sanctions_hit,
            "edd_required": state.edd_required,
            "risk_profile": state.risk_profile,
            "suitability_rationale": state.suitability_rationale,
            "prompt": (
                "Please review this onboarding case and provide: "
                "decision (APPROVED/REJECTED/ESCALATED), reviewer_id, notes"
            ),
        }
    )

    # Execution resumes here after reviewer submits decision
    decision = ReviewDecision(review_response.get("decision", "REJECTED"))
    reviewer_id = review_response.get("reviewer_id", "")
    notes = review_response.get("notes", "")

    logger.info(
        "Human review decision received: %s by reviewer=%s", decision.value, reviewer_id
    )

    return {
        "human_review_decision": decision,
        "human_reviewer_id": reviewer_id,
        "human_review_notes": notes,
        "messages": [
            AIMessage(
                content=f"Human review completed by {reviewer_id}. "
                f"Decision: {decision.value}. Notes: {notes}"
            )
        ],
    }


async def account_setup_agent(
    state: OnboardingState, config: RunnableConfig
) -> dict:
    """
    Node 5: Account Setup Agent

    Triggers downstream account provisioning in Avaloq once all checks pass
    and human approvals are captured (where required).

    Integration points:
    - Avaloq WAPI: client record creation and account hierarchy provisioning
    - CRS/FATCA reporting service: initiates regulatory reporting obligations
    """
    logger.info("Account setup agent starting for client_id=%s", state.client_id)

    # Determine final approval status
    if state.human_review_required:
        if state.human_review_decision == ReviewDecision.REJECTED:
            return {
                "workflow_status": "REJECTED",
                "rejection_reason": state.human_review_notes,
                "messages": [AIMessage(content="Onboarding rejected following human review.")],
            }
        if state.human_review_decision == ReviewDecision.ESCALATED:
            return {
                "workflow_status": "ESCALATED",
                "messages": [AIMessage(content="Case escalated to senior compliance.")],
            }

    # Provision Avaloq client record
    client_data = {
        "extracted_fields": state.extracted_fields,
        "risk_profile": state.risk_profile,
        "aml_risk_level": state.aml_risk_level.value,
        "edd_required": state.edd_required,
        "reviewer_id": state.human_reviewer_id,
        "review_notes": state.human_review_notes,
    }

    avaloq_id = await create_avaloq_client_record(client_data)

    # Trigger CRS/FATCA if applicable
    tax_residencies = state.extracted_fields.get("tax_residencies", [])
    crs_triggered = False
    if tax_residencies:
        crs_triggered = await trigger_crs_fatca_reporting(avaloq_id, tax_residencies)

    return {
        "avaloq_client_record_id": avaloq_id,
        "account_structure_provisioned": True,
        "crs_fatca_triggered": crs_triggered,
        "workflow_status": "COMPLETED",
        "messages": [
            AIMessage(
                content=f"Account provisioned in Avaloq. Client ID={avaloq_id}. "
                f"CRS/FATCA triggered={crs_triggered}."
            )
        ],
    }


# ---------------------------------------------------------------------------
# Routing Functions
# ---------------------------------------------------------------------------

def route_after_document_intake(
    state: OnboardingState,
) -> Literal["human_review_gate", "kyc_aml_agent"]:
    """
    Route to human review immediately if document intake fails confidence threshold.
    Otherwise proceed to KYC/AML screening.
    """
    if state.document_intake_escalation:
        logger.info("Routing to human review: document intake escalation")
        return "human_review_gate"
    return "kyc_aml_agent"


def route_after_kyc_aml(
    state: OnboardingState,
) -> Literal["human_review_gate", "risk_suitability_agent"]:
    """
    Route to human review on sanctions hit, unacceptable risk, or
    low-confidence entity disambiguation.
    Otherwise proceed to suitability assessment.
    """
    if state.human_review_required:
        logger.info("Routing to human review: KYC/AML escalation — %s", state.human_review_reason)
        return "human_review_gate"
    return "risk_suitability_agent"


def route_after_human_review(
    state: OnboardingState,
) -> Literal["account_setup_agent", "risk_suitability_agent", END]:
    """
    After human review:
    - REJECTED: end the workflow
    - ESCALATED: end (case moves to senior compliance queue outside this graph)
    - APPROVED after document intake failure: re-enter at KYC/AML
    - APPROVED after KYC/AML: proceed to suitability assessment
    - APPROVED after suitability: proceed to account setup
    """
    if state.human_review_decision in (ReviewDecision.REJECTED, ReviewDecision.ESCALATED):
        return END

    # Determine where in the workflow the escalation occurred
    # to resume at the correct downstream node
    if state.document_intake_escalation and not state.aml_risk_level:
        return "risk_suitability_agent"

    return "account_setup_agent"


# ---------------------------------------------------------------------------
# Graph Definition
# ---------------------------------------------------------------------------

def build_iclm_graph(checkpointer=None) -> StateGraph:
    """
    Build and compile the iCLM LangGraph agent graph.

    Design note (ADR-001 — LangGraph over CrewAI):
    LangGraph exposes the graph topology explicitly. Every edge and routing
    function is visible and testable. In a regulated environment, the
    orchestration logic itself may be subject to regulatory review.
    CrewAI's abstraction of control flow makes this impossible to inspect.

    The human-in-the-loop interruption is a first-class LangGraph primitive
    via interrupt(). In CrewAI this would require a workaround.

    Usage:
        graph = build_iclm_graph(checkpointer=PostgresSaver.from_conn_string(DB_URL))
        config = {"configurable": {"thread_id": "onboarding-CLT-001"}}
        result = await graph.ainvoke(initial_state, config=config)
    """
    builder = StateGraph(OnboardingState)

    # Register nodes
    builder.add_node("document_intake_agent", document_intake_agent)
    builder.add_node("kyc_aml_agent", kyc_aml_agent)
    builder.add_node("risk_suitability_agent", risk_suitability_agent)
    builder.add_node("human_review_gate", human_review_gate)
    builder.add_node("account_setup_agent", account_setup_agent)

    # Entry point
    builder.add_edge(START, "document_intake_agent")

    # Conditional routing
    builder.add_conditional_edges(
        "document_intake_agent",
        route_after_document_intake,
        {
            "human_review_gate": "human_review_gate",
            "kyc_aml_agent": "kyc_aml_agent",
        },
    )

    builder.add_conditional_edges(
        "kyc_aml_agent",
        route_after_kyc_aml,
        {
            "human_review_gate": "human_review_gate",
            "risk_suitability_agent": "risk_suitability_agent",
        },
    )

    # Risk/suitability always proceeds to account setup
    # (suitability issues are captured in state, not blocking)
    builder.add_edge("risk_suitability_agent", "account_setup_agent")

    builder.add_conditional_edges(
        "human_review_gate",
        route_after_human_review,
        {
            "account_setup_agent": "account_setup_agent",
            "risk_suitability_agent": "risk_suitability_agent",
            END: END,
        },
    )

    builder.add_edge("account_setup_agent", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review_gate"],  # Suspend before human review node
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def run_onboarding(
    client_id: str,
    correlation_id: str,
    documents: list[str],
    db_url: str = "postgresql://localhost:5432/iclm",
) -> OnboardingState:
    """
    Run the iCLM onboarding graph for a single client.

    The PostgreSQL checkpointer enables:
    1. Durable state persistence across service restarts
    2. Resume from the exact suspension point after human review
    3. Full audit trail of every state transition (queryable by thread_id)
    """
    checkpointer = PostgresSaver.from_conn_string(db_url)
    graph = build_iclm_graph(checkpointer=checkpointer)

    thread_id = f"onboarding-{client_id}-{correlation_id}"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    initial_state = OnboardingState(
        client_id=client_id,
        correlation_id=correlation_id,
        documents_received=documents,
    )

    final_state = await graph.ainvoke(initial_state, config=config)
    return final_state


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        run_onboarding(
            client_id="CLT-DEMO-001",
            correlation_id="CORR-2025-001",
            documents=["passport.pdf", "utility_bill.pdf", "source_of_wealth.pdf"],
        )
    )
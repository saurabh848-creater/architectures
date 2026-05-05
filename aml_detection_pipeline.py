"""
AI-Powered Unified AML Screening Platform
Hybrid Detection Pipeline: Rules + ML Anomaly Detection + LLM Entity Disambiguation

Architecture Reference: 02-aml-hybrid-detection.md

Design decisions reflected here:
- Hybrid stack over single-model (ADR-001): rules for deterministic regulatory
  requirements, ML for statistical behavioural anomalies, LLM for semantic
  entity disambiguation. Each layer independently auditable.
- Neo4j for entity relationships (ADR-002): multi-hop queries are O(n) in a
  graph DB vs O(n^k) with recursive SQL joins at Tier 1 transaction volumes.
- Private LLM endpoint (ADR-003): client PII (names, DOBs, nationalities)
  is sensitive personal data under GDPR. Azure OpenAI via private VNet endpoint,
  no-logging configuration. No data leaves the bank's cloud boundary.
- Composite scorer: weighted ensemble outputs, not a voting ensemble.
  Each layer's signal is preserved independently for investigator explainability.

Status: Skeleton — external API calls (World-Check, Factiva, Neo4j) are stubbed.
        Production requires live screening credentials and MLflow model registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
from langchain_core.messages import HumanMessage
from langchain_openai import AzureChatOpenAI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain Types
# ---------------------------------------------------------------------------

class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DetectionLayerSignal(str, Enum):
    RULES = "RULES"
    ML_ISOLATION_FOREST = "ML_ISOLATION_FOREST"
    ML_LSTM = "ML_LSTM"
    LLM_DISAMBIGUATION = "LLM_DISAMBIGUATION"
    GRAPH_NETWORK = "GRAPH_NETWORK"


@dataclass
class Transaction:
    transaction_id: str
    client_id: str
    amount: float
    currency: str
    counterparty_id: str
    counterparty_jurisdiction: str
    channel: str
    product_type: str
    timestamp: str


@dataclass
class ClientProfile:
    client_id: str
    full_name: str
    date_of_birth: str
    nationality: str
    country_of_residence: str
    client_segment: str  # RETAIL, HNW, UHNW


@dataclass
class DetectionLayerResult:
    """
    Result from a single detection layer.

    Design note: Each layer produces an independent signal with its own
    confidence score. The composite scorer combines these with calibrated
    weights — it does NOT use a voting ensemble. This preserves
    explainability: an investigator can see which layer flagged and why.
    """
    layer: DetectionLayerSignal
    flagged: bool
    confidence: float  # 0.0 to 1.0
    risk_contribution: float  # Contribution to composite score
    explanation: str
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompositeAMLAlert:
    """
    Unified alert surfaced to the investigator workbench.

    Contains the composite risk score, per-layer breakdown,
    graph intelligence context, and LLM-generated plain-English rationale.
    All fields required for audit trail: model version, feature snapshot,
    alert ID, decision timestamp.
    """
    alert_id: str
    client_id: str
    transaction_id: str
    composite_risk_score: float  # 0.0 to 1.0
    severity: AlertSeverity
    layer_results: list[DetectionLayerResult]
    graph_context: dict[str, Any]  # Neo4j subgraph context
    llm_rationale: str  # Plain-English explanation for investigator
    model_versions: dict[str, str]  # Audit: which model version produced each signal
    suggested_action: str  # DISMISS / INVESTIGATE / ESCALATE / RESTRICT
    shap_values: dict[str, float] = field(default_factory=dict)  # ML explainability


# ---------------------------------------------------------------------------
# Layer 1: Rules Engine
# ---------------------------------------------------------------------------

class RulesEngine:
    """
    Deterministic, threshold-based detection.

    Design note: Rules are version-controlled and changes require four-eyes
    approval. This maintains the audit trail required by FCA SYSC and
    JMLSG guidance. Rules are loaded from a versioned rule store, not
    hard-coded, so they can be updated without code deployments.
    """

    # Jurisdiction-specific reporting thresholds (USD equivalent)
    REPORTING_THRESHOLDS: dict[str, float] = {
        "US": 10_000,
        "EU": 10_000,
        "UK": 10_000,
        "DEFAULT": 10_000,
    }

    # High-risk jurisdictions (FATF grey/black list — update from config in production)
    HIGH_RISK_JURISDICTIONS: set[str] = {
        "AF", "KP", "IR", "MM", "SY",  # Current FATF high-risk
    }

    # Velocity rule: max transactions to high-risk jurisdiction in window
    VELOCITY_THRESHOLD = 5
    VELOCITY_WINDOW_HOURS = 48

    def evaluate(self, transaction: Transaction, client_profile: ClientProfile) -> DetectionLayerResult:
        """
        Evaluate all rules against the transaction.
        Returns the highest-severity rule match, or a clean result.
        """
        flags = []

        # Rule 1: Reporting threshold breach
        threshold = self.REPORTING_THRESHOLDS.get(
            transaction.counterparty_jurisdiction, self.REPORTING_THRESHOLDS["DEFAULT"]
        )
        if transaction.amount >= threshold:
            flags.append(
                f"Transaction amount {transaction.amount} {transaction.currency} "
                f"meets/exceeds reporting threshold {threshold} for jurisdiction "
                f"{transaction.counterparty_jurisdiction}"
            )

        # Rule 2: High-risk jurisdiction
        if transaction.counterparty_jurisdiction in self.HIGH_RISK_JURISDICTIONS:
            flags.append(
                f"Counterparty domiciled in FATF high-risk jurisdiction: "
                f"{transaction.counterparty_jurisdiction}"
            )

        # Rule 3: Velocity check (stub — production queries time-windowed transaction store)
        recent_high_risk_count = self._get_recent_high_risk_tx_count(
            transaction.client_id, self.VELOCITY_WINDOW_HOURS
        )
        if recent_high_risk_count >= self.VELOCITY_THRESHOLD:
            flags.append(
                f"Velocity rule: {recent_high_risk_count} transactions to high-risk "
                f"jurisdictions in {self.VELOCITY_WINDOW_HOURS}h window"
            )

        flagged = len(flags) > 0
        explanation = " | ".join(flags) if flags else "No rules triggered"

        return DetectionLayerResult(
            layer=DetectionLayerSignal.RULES,
            flagged=flagged,
            confidence=1.0 if flagged else 0.0,  # Rules are deterministic — no confidence range
            risk_contribution=0.40 if flagged else 0.0,  # Rules carry highest weight in composite
            explanation=explanation,
            raw_output={"rules_triggered": flags},
        )

    def _get_recent_high_risk_tx_count(self, client_id: str, hours: int) -> int:
        """Query time-windowed transaction store for velocity check."""
        # TODO: Query BigQuery / Cloud SQL for time-windowed aggregation
        return 0


# ---------------------------------------------------------------------------
# Layer 2: ML Anomaly Detection
# ---------------------------------------------------------------------------

class IsolationForestDetector:
    """
    Transaction-level anomaly detection.

    Detects statistical outliers in transaction features without requiring
    labelled fraud data. Effective for structuring pattern detection.

    Design note: Runs in parallel with LSTM. Outputs feed into composite
    scorer as independent signals with calibrated weights — not as a
    voting ensemble. This preserves interpretability per layer.

    Model governance:
    - Registered in MLflow model registry
    - PSI drift check weekly (threshold: 0.2)
    - Shadow run for 30 days before any model cutover
    - SHAP values logged for every scoring call
    """

    def __init__(self, model_version: str = "v2.1.0"):
        self.model_version = model_version
        # TODO: Load from MLflow model registry
        # self.model = mlflow.sklearn.load_model(f"models:/aml-isolation-forest/{model_version}")
        self.model = None

    def score(self, transaction: Transaction) -> DetectionLayerResult:
        """
        Score a transaction for statistical anomaly.
        Returns anomaly score and SHAP feature contributions.
        """
        features = self._extract_features(transaction)

        # TODO: Replace with live model inference
        # raw_score = self.model.decision_function([features])[0]
        # anomaly_score = 1 - (raw_score + 0.5)  # Normalise to 0-1
        anomaly_score = 0.15  # Stub

        flagged = anomaly_score > 0.65  # Configurable threshold

        # TODO: shap.TreeExplainer for feature-level explainability
        shap_values = {
            "amount": 0.12,
            "counterparty_jurisdiction_risk": 0.08,
            "time_of_day": 0.03,
        }

        return DetectionLayerResult(
            layer=DetectionLayerSignal.ML_ISOLATION_FOREST,
            flagged=flagged,
            confidence=anomaly_score,
            risk_contribution=anomaly_score * 0.25,  # Weight in composite
            explanation=(
                f"Isolation Forest anomaly score: {anomaly_score:.3f}. "
                f"Top contributing feature: {max(shap_values, key=shap_values.get)}"
            ),
            raw_output={"anomaly_score": anomaly_score, "shap_values": shap_values},
        )

    def _extract_features(self, transaction: Transaction) -> list[float]:
        """
        Feature engineering for Isolation Forest.
        Numerical features only — the model does not handle strings.
        """
        # TODO: Full feature pipeline: amount normalisation, jurisdiction risk encoding,
        # time-of-day cyclical encoding, product type one-hot, channel one-hot
        return [
            transaction.amount,
            1.0 if transaction.counterparty_jurisdiction in RulesEngine.HIGH_RISK_JURISDICTIONS else 0.0,
        ]


class LSTMBehaviourDetector:
    """
    Longitudinal behavioural anomaly detection.

    Trained on 90-day transaction sequences per client segment.
    Flags when current 30-day behaviour departs from client's modelled baseline.
    Effective for detecting account takeover and gradual money laundering patterns.

    Design note: LSTM uses sequence-level features (transaction ordering matters).
    Isolation Forest uses point-level features (ordering does not matter).
    They detect different anomaly patterns — running both in parallel is not
    redundant; it is complementary.
    """

    def __init__(self, model_version: str = "v1.3.0"):
        self.model_version = model_version
        # TODO: Load TensorFlow/Keras model from GCS or MLflow
        self.model = None

    def score(
        self, client_id: str, transaction_sequence: list[Transaction]
    ) -> DetectionLayerResult:
        """
        Score a client's recent transaction sequence for behavioural departure.
        """
        if len(transaction_sequence) < 10:
            # Insufficient sequence history for meaningful scoring
            return DetectionLayerResult(
                layer=DetectionLayerSignal.ML_LSTM,
                flagged=False,
                confidence=0.0,
                risk_contribution=0.0,
                explanation="Insufficient transaction history for LSTM scoring (min 10 transactions)",
            )

        sequence_features = self._extract_sequence_features(transaction_sequence)

        # TODO: TF model inference
        # reconstruction_error = self.model.predict(sequence_features)
        # behavioural_score = min(reconstruction_error / THRESHOLD, 1.0)
        behavioural_score = 0.12  # Stub

        flagged = behavioural_score > 0.70

        return DetectionLayerResult(
            layer=DetectionLayerSignal.ML_LSTM,
            flagged=flagged,
            confidence=behavioural_score,
            risk_contribution=behavioural_score * 0.20,
            explanation=(
                f"LSTM behavioural departure score: {behavioural_score:.3f}. "
                f"Trained on 90-day baseline for segment={transaction_sequence[-1].channel}"
            ),
            raw_output={"behavioural_score": behavioural_score},
        )

    def _extract_sequence_features(self, transactions: list[Transaction]) -> np.ndarray:
        """Build fixed-length sequence feature matrix for LSTM input."""
        # TODO: Full sequence feature pipeline
        return np.zeros((len(transactions), 10))


# ---------------------------------------------------------------------------
# Layer 3: LLM Entity Disambiguation
# ---------------------------------------------------------------------------

class LLMEntityDisambiguator:
    """
    Semantic and contextual disambiguation for name/alias matching.

    Addresses the core limitation of fuzzy string matching against
    sanctions and PEP lists: "Mohammed Al-Hassan" returns hundreds of
    candidates. Traditional fuzzy matching cannot contextualise these
    against a client profile. LLM-based disambiguation can.

    Design note (ADR-003): Azure OpenAI Service within the bank's VNet,
    accessed via private endpoint. No-logging configuration. Client PII
    never leaves the bank's cloud boundary.

    Output includes confidence score and plain-English reasoning.
    Below 0.80 confidence: flag for human review.
    """

    CONFIDENCE_THRESHOLD_FOR_AUTO_CLEAR = 0.80

    def __init__(self):
        self.llm = AzureChatOpenAI(
            azure_deployment="gpt-4o",
            api_version="2024-08-01-preview",
            temperature=0,
            # Private endpoint — no internet egress
        )

    async def disambiguate(
        self,
        client_profile: ClientProfile,
        candidate_match: dict[str, Any],
        prior_decisions_context: list[str],  # RAG-retrieved prior disambiguation decisions
    ) -> DetectionLayerResult:
        """
        Determine whether a candidate sanctions/PEP match is a true match
        for this specific client profile.
        """
        prompt = f"""
You are an AML entity disambiguation specialist at a regulated financial institution.

CLIENT PROFILE:
Name: {client_profile.full_name}
Date of Birth: {client_profile.date_of_birth}
Nationality: {client_profile.nationality}
Country of Residence: {client_profile.country_of_residence}

CANDIDATE SANCTIONS/PEP MATCH:
Name on List: {candidate_match.get('name', 'UNKNOWN')}
Aliases: {candidate_match.get('aliases', [])}
Date of Birth on List: {candidate_match.get('dob', 'UNKNOWN')}
Nationality on List: {candidate_match.get('nationality', 'UNKNOWN')}
List Type: {candidate_match.get('list_type', 'UNKNOWN')}

CONTEXT FROM PRIOR DISAMBIGUATION DECISIONS:
{chr(10).join(prior_decisions_context) if prior_decisions_context else 'No prior decisions available.'}

TASK:
Determine whether the candidate match is the same person as the client profile.
Consider: name similarity, date of birth, nationality, aliases, and any contextual signals.

Respond ONLY in this format:
DECISION: TRUE_MATCH | FALSE_MATCH | UNCERTAIN
CONFIDENCE: <float 0.00-1.00>
REASONING: <concise explanation, max 100 words, citing specific matching or non-matching attributes>
"""

        response = await self.llm.ainvoke([HumanMessage(content=prompt)])
        response_text = response.content

        # Parse response
        is_match = "TRUE_MATCH" in response_text
        is_uncertain = "UNCERTAIN" in response_text

        # TODO: Robust parsing of CONFIDENCE and REASONING fields
        confidence = 0.92 if not is_uncertain else 0.55
        reasoning = response_text  # TODO: Extract REASONING section

        requires_human_review = (
            is_match or is_uncertain or confidence < self.CONFIDENCE_THRESHOLD_FOR_AUTO_CLEAR
        )

        return DetectionLayerResult(
            layer=DetectionLayerSignal.LLM_DISAMBIGUATION,
            flagged=is_match or is_uncertain,
            confidence=confidence,
            risk_contribution=0.35 if is_match else (0.15 if is_uncertain else 0.0),
            explanation=f"LLM disambiguation: {reasoning[:200]}",
            raw_output={
                "decision": "TRUE_MATCH" if is_match else "UNCERTAIN" if is_uncertain else "FALSE_MATCH",
                "confidence": confidence,
                "requires_human_review": requires_human_review,
                "full_reasoning": response_text,
            },
        )


# ---------------------------------------------------------------------------
# Graph Intelligence Layer
# ---------------------------------------------------------------------------

class GraphIntelligenceLayer:
    """
    Neo4j entity graph analytics.

    Surfaces hidden entity relationships and prioritises alerts by
    network-level risk exposure.

    Design note (ADR-002 — Neo4j over relational store):
    Multi-hop relationship queries (find all clients within 3 hops of a
    sanctioned entity) are O(n) in a graph DB vs O(n^k) with recursive
    SQL joins. At Tier 1 bank transaction volumes, this is not a marginal
    difference — it is the difference between milliseconds and minutes.

    Key graph queries:
    - Shortest path from client to any sanctioned node (within 3 hops)
    - Community detection for transaction cluster identification
    - PageRank to identify high-influence nodes in transaction network
    """

    def __init__(self, neo4j_uri: str = "bolt://localhost:7687"):
        self.neo4j_uri = neo4j_uri
        # TODO: from neo4j import GraphDatabase; self.driver = GraphDatabase.driver(...)

    async def get_network_context(self, client_id: str) -> dict[str, Any]:
        """
        Retrieve network risk context for a client from the entity graph.

        Returns:
        - hops_to_nearest_sanctioned_entity: int (None if no path within 3 hops)
        - transaction_cluster_risk: float
        - pagerank_score: float
        - connected_pep_count: int
        """
        # TODO: Cypher queries against Neo4j
        # Shortest path to sanctioned node:
        # MATCH (c:Client {id: $client_id}), (s:SanctionsEntry)
        # MATCH p = shortestPath((c)-[*..3]-(s))
        # RETURN length(p) AS hops

        # Community detection:
        # CALL gds.louvain.stream('transaction-graph') YIELD nodeId, communityId
        # WHERE gds.util.asNode(nodeId).id = $client_id

        return {
            "hops_to_nearest_sanctioned_entity": None,
            "transaction_cluster_risk": 0.05,
            "pagerank_score": 0.12,
            "connected_pep_count": 0,
            "subgraph_visualisation_url": f"https://aml-workbench/graph/{client_id}",
        }

    def calculate_graph_risk_contribution(self, network_context: dict) -> float:
        """Convert network context to a risk score contribution."""
        score = 0.0
        hops = network_context.get("hops_to_nearest_sanctioned_entity")
        if hops is not None:
            # Closer to sanctioned entity = higher risk contribution
            score += max(0.0, (3 - hops) / 3) * 0.30  # Up to 0.30 contribution
        score += network_context.get("transaction_cluster_risk", 0.0) * 0.10
        score += min(network_context.get("connected_pep_count", 0) * 0.05, 0.15)
        return min(score, 0.55)  # Cap graph contribution


# ---------------------------------------------------------------------------
# Composite Scorer and Alert Assembly
# ---------------------------------------------------------------------------

class CompositeScorer:
    """
    Weighted combination of all detection layer signals.

    Layer weights (calibrated against historical investigation outcomes):
    - Rules: 0.40 (highest — deterministic regulatory requirements)
    - LLM Disambiguation: 0.35 (second — semantic matching is decisive)
    - Isolation Forest: 0.25 (statistical anomaly, lower weight)
    - LSTM: 0.20 (behavioural, lower weight — requires history)
    - Graph: 0.55 max (capped — network risk is amplifying, not primary)

    Note: These weights sum to >1.0 intentionally. The composite score is
    clamped to [0.0, 1.0]. Each layer's contribution is stored separately
    in the alert for investigator explainability.
    """

    SEVERITY_THRESHOLDS = {
        AlertSeverity.CRITICAL: 0.80,
        AlertSeverity.HIGH: 0.60,
        AlertSeverity.MEDIUM: 0.40,
        AlertSeverity.LOW: 0.0,
    }

    def score(self, layer_results: list[DetectionLayerResult], graph_context: dict) -> tuple[float, AlertSeverity]:
        """Calculate composite score and severity from all layer signals."""
        composite = sum(r.risk_contribution for r in layer_results)
        composite = min(composite, 1.0)

        severity = AlertSeverity.LOW
        for sev, threshold in self.SEVERITY_THRESHOLDS.items():
            if composite >= threshold:
                severity = sev
                break

        return composite, severity

    async def generate_investigator_rationale(
        self,
        alert: CompositeAMLAlert,
        transaction: Transaction,
        client_profile: ClientProfile,
    ) -> str:
        """
        Generate plain-English rationale for the investigator workbench.

        The rationale includes:
        - Which detection layers triggered and at what confidence
        - Network graph context
        - Suggested action with reasoning
        This replaces the opaque "alert score: 0.85" that investigators
        cannot act on efficiently.
        """
        llm = AzureChatOpenAI(azure_deployment="gpt-4o", temperature=0)

        triggered_layers = [r for r in alert.layer_results if r.flagged]
        layer_summary = "\n".join(
            f"- {r.layer.value}: {r.explanation}" for r in triggered_layers
        )

        prompt = f"""
You are an AML analyst assistant. Generate a concise, plain-English investigation
rationale for a compliance officer reviewing this alert.

CLIENT: {client_profile.full_name} ({client_profile.nationality})
TRANSACTION: {transaction.amount} {transaction.currency} to {transaction.counterparty_jurisdiction}
COMPOSITE RISK SCORE: {alert.composite_risk_score:.2f} ({alert.severity.value})

TRIGGERED DETECTION SIGNALS:
{layer_summary}

NETWORK CONTEXT:
- Hops to nearest sanctioned entity: {alert.graph_context.get('hops_to_nearest_sanctioned_entity', 'N/A')}
- Connected PEP count: {alert.graph_context.get('connected_pep_count', 0)}

Write a 3-sentence investigation rationale: (1) what triggered the alert,
(2) the key risk factors, (3) recommended action.
"""
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return response.content


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class AMLScreeningPipeline:
    """
    Unified AML screening pipeline orchestrator.

    Coordinates all detection layers and assembles the composite alert.
    Designed to run as a Kafka consumer: each transaction event triggers
    one pipeline execution, which produces one alert event (or no-alert).

    Production deployment: GKE pod, Kafka consumer group, one pod per
    Kafka partition for horizontal scaling.
    """

    def __init__(self):
        self.rules_engine = RulesEngine()
        self.isolation_forest = IsolationForestDetector()
        self.lstm_detector = LSTMBehaviourDetector()
        self.llm_disambiguator = LLMEntityDisambiguator()
        self.graph_layer = GraphIntelligenceLayer()
        self.composite_scorer = CompositeScorer()

    async def process(
        self,
        transaction: Transaction,
        client_profile: ClientProfile,
        transaction_history: list[Transaction],
        candidate_sanctions_matches: list[dict],
    ) -> CompositeAMLAlert | None:
        """
        Run the full hybrid detection pipeline for one transaction.

        Returns a CompositeAMLAlert if any layer flags the transaction,
        or None if all layers are clean (no alert generated).
        """
        layer_results: list[DetectionLayerResult] = []

        # Layer 1: Rules (synchronous — deterministic, fast)
        rules_result = self.rules_engine.evaluate(transaction, client_profile)
        layer_results.append(rules_result)

        # Layer 2: ML models (run in parallel for efficiency)
        # In production: asyncio.gather with run_in_executor for CPU-bound ML inference
        isolation_result = self.isolation_forest.score(transaction)
        lstm_result = self.lstm_detector.score(client_profile.client_id, transaction_history)
        layer_results.extend([isolation_result, lstm_result])

        # Layer 3: LLM disambiguation (only if candidate matches exist)
        prior_decisions: list[str] = []  # TODO: RAG retrieval from Pinecone
        for candidate in candidate_sanctions_matches:
            disambiguation_result = await self.llm_disambiguator.disambiguate(
                client_profile, candidate, prior_decisions
            )
            layer_results.append(disambiguation_result)

        # Graph intelligence
        graph_context = await self.graph_layer.get_network_context(client_profile.client_id)

        # Composite score
        composite_score, severity = self.composite_scorer.score(layer_results, graph_context)

        # No alert if composite score is below minimum threshold
        if composite_score < 0.15 and not any(r.flagged for r in layer_results):
            logger.debug("No alert for transaction_id=%s (score=%.3f)", transaction.transaction_id, composite_score)
            return None

        # Assemble alert
        import uuid
        alert = CompositeAMLAlert(
            alert_id=str(uuid.uuid4()),
            client_id=client_profile.client_id,
            transaction_id=transaction.transaction_id,
            composite_risk_score=composite_score,
            severity=severity,
            layer_results=layer_results,
            graph_context=graph_context,
            llm_rationale="",  # Populated below
            model_versions={
                "isolation_forest": self.isolation_forest.model_version,
                "lstm": self.lstm_detector.model_version,
            },
            suggested_action=self._determine_suggested_action(severity, layer_results),
        )

        # Generate investigator rationale
        alert.llm_rationale = await self.composite_scorer.generate_investigator_rationale(
            alert, transaction, client_profile
        )

        logger.info(
            "Alert generated: alert_id=%s client_id=%s score=%.3f severity=%s",
            alert.alert_id, alert.client_id, alert.composite_risk_score, alert.severity.value
        )

        return alert

    def _determine_suggested_action(
        self, severity: AlertSeverity, layer_results: list[DetectionLayerResult]
    ) -> str:
        """Rules-based suggested action from severity and layer signals."""
        # Direct sanctions hit always triggers restriction
        for r in layer_results:
            if r.layer == DetectionLayerSignal.LLM_DISAMBIGUATION and r.flagged:
                if r.raw_output.get("decision") == "TRUE_MATCH":
                    return "RESTRICT_AND_FILE_SAR"

        if severity == AlertSeverity.CRITICAL:
            return "ESCALATE_TO_SENIOR_COMPLIANCE"
        elif severity == AlertSeverity.HIGH:
            return "INVESTIGATE"
        elif severity == AlertSeverity.MEDIUM:
            return "INVESTIGATE"
        else:
            return "DISMISS"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main():
    """Demonstrate pipeline with stub data."""
    pipeline = AMLScreeningPipeline()

    transaction = Transaction(
        transaction_id="TXN-DEMO-001",
        client_id="CLT-001",
        amount=15_000.0,
        currency="GBP",
        counterparty_id="CTPTY-001",
        counterparty_jurisdiction="IR",
        channel="ONLINE",
        product_type="WIRE_TRANSFER",
        timestamp="2025-06-01T14:23:00Z",
    )

    client_profile = ClientProfile(
        client_id="CLT-001",
        full_name="John Smith",
        date_of_birth="1975-04-15",
        nationality="GB",
        country_of_residence="GB",
        client_segment="HNW",
    )

    alert = await pipeline.process(
        transaction=transaction,
        client_profile=client_profile,
        transaction_history=[],
        candidate_sanctions_matches=[],
    )

    if alert:
        print(f"Alert generated: {alert.alert_id}")
        print(f"Severity: {alert.severity.value}")
        print(f"Score: {alert.composite_risk_score:.3f}")
        print(f"Suggested action: {alert.suggested_action}")
        print(f"\nRationale:\n{alert.llm_rationale}")
    else:
        print("No alert generated.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
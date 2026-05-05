"""
Perpetual KYC (pKYC) — Event-Driven CLM Platform
Event Processor: Risk Delta Detection, Classification, and Review Routing

Architecture Reference: 03-pkyc-event-driven-platform.md

Design decisions reflected here:
- Event-driven over request-response (ADR-001 — Gregor Hohpe canonical pattern):
  KYC state changes are facts that occurred. Modelled as immutable domain events,
  not API calls. Multiple downstream consumers react to the same event via fan-out.
  Event replay enables regulatory audit (what did the bank know and when?).
- Append-only event store (ADR-003 — Martin Fowler Event Sourcing):
  Mutable client record cannot satisfy FCA SYSC 3.2 record-keeping requirements.
  The event store is the system of truth. Azure SQL client master is a
  materialised projection (read model) derived by replaying the event log.
- Microservices with strangler fig (ADR-002 — Sam Newman):
  Pega retained for case workflow only. Event processor, screening pipeline,
  and digital journey are purpose-built microservices around it.
- Eventual consistency accepted: client risk status may take seconds to
  propagate. For pKYC (not payment processing), this is acceptable.

Status: Skeleton — Azure Event Grid, Factiva, and World-Check calls are stubbed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain Events (Immutable Facts)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """
    All pKYC domain event types.

    Design note: Events are immutable facts (something that happened),
    not commands (something you want to happen). The event processor
    decides what action to take — the event does not prescribe it.
    This is Gregor Hohpe's canonical EDA pattern.
    """
    ADVERSE_MEDIA_DETECTED = "kyc.risk.adverse_media.detected"
    PEP_STATUS_CHANGED = "kyc.risk.pep_status.changed"
    SANCTIONS_ALERT = "kyc.risk.sanctions.alert"
    OWNERSHIP_STRUCTURE_CHANGED = "kyc.risk.ownership.changed"
    JURISDICTION_RISK_CHANGED = "kyc.risk.jurisdiction.changed"
    CLIENT_SELF_DECLARED_CHANGE = "kyc.client.self_declared.change"
    SCHEDULED_REVIEW_DUE = "kyc.review.scheduled.due"


class RiskSignalSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReviewAction(str, Enum):
    """
    Routing actions available to the event processor.

    Design note: The action is determined by the event processor based on
    risk delta calculation — it is not embedded in the event itself.
    """
    NO_ACTION = "NO_ACTION"           # Auto-update client record, no review
    SELF_SERVE_REFRESH = "SELF_SERVE_REFRESH"   # Targeted document request, client self-serve
    COMPLIANCE_REVIEW = "COMPLIANCE_REVIEW"     # Full compliance officer review
    IMMEDIATE_ESCALATION = "IMMEDIATE_ESCALATION"  # Account restriction + senior escalation


@dataclass(frozen=True)
class RiskSignal:
    """
    Structured risk signal within a domain event.
    Immutable value object.
    """
    signal_type: str
    description: str
    severity: RiskSignalSeverity
    confidence: float
    source_system: str
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class PKYCDomainEvent:
    """
    Immutable domain event.

    Versioned from the outset to support schema evolution without breaking consumers.
    correlationId enables distributed tracing across the full pKYC chain.

    Design note: recommendedAction is a signal from the source system,
    not a directive. The event processor makes the final routing decision
    using its own risk delta calculation against the current client state.
    """
    event_id: str
    event_type: EventType
    event_version: str
    timestamp: str
    source_system: str
    client_id: str
    client_segment: str
    correlation_id: str
    risk_signal: RiskSignal
    schema_version: str = "1.0"
    producer_service: str = ""

    @classmethod
    def create(
        cls,
        event_type: EventType,
        client_id: str,
        client_segment: str,
        source_system: str,
        risk_signal: RiskSignal,
        producer_service: str = "",
    ) -> "PKYCDomainEvent":
        return cls(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            event_version="1.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            source_system=source_system,
            client_id=client_id,
            client_segment=client_segment,
            correlation_id=str(uuid.uuid4()),
            risk_signal=risk_signal,
            producer_service=producer_service,
        )


# ---------------------------------------------------------------------------
# Client State (Read Model / Materialised Projection)
# ---------------------------------------------------------------------------

@dataclass
class ClientKYCState:
    """
    Current materialised KYC state for a client.

    Design note (ADR-003 — Event Sourcing):
    This is a READ MODEL derived by replaying the event log from the
    append-only event store. It is NOT the system of truth.

    The event store (Azure Cosmos DB, append-only) is the system of truth.
    FCA SYSC 3.2 requires the bank to demonstrate what it knew about a client
    and when — a mutable record cannot satisfy this.

    This projection is stored in Azure SQL for operational query efficiency.
    If the projection is lost or corrupt, it can be rebuilt by replaying
    the event store from the beginning.
    """
    client_id: str
    full_name: str
    client_segment: str  # RETAIL, HNW, UHNW
    current_risk_rating: str  # LOW, MEDIUM, HIGH
    jurisdiction: str
    pep_status: bool = False
    sanctions_status: bool = False
    last_full_review_date: str = ""
    last_risk_change_date: str = ""
    open_review_cases: int = 0
    beneficial_ownership_pct: float = 0.0


@dataclass
class RiskDelta:
    """
    Calculated change in risk exposure from a new risk signal.

    Used by the routing engine to determine what action to trigger.
    A high delta requires a compliance officer review.
    A low delta can be auto-applied to the client record without review.
    """
    current_risk_rating: str
    new_risk_indicator: str
    delta_magnitude: float  # 0.0 to 1.0
    requires_review: bool
    recommended_action: ReviewAction
    delta_reasoning: str


# ---------------------------------------------------------------------------
# Risk Delta Calculator
# ---------------------------------------------------------------------------

class RiskDeltaCalculator:
    """
    Calculates the change in client risk from a new event signal.

    Determines the appropriate ReviewAction based on:
    1. Current client risk rating
    2. Risk signal severity
    3. Event type (sanctions always = IMMEDIATE_ESCALATION)
    4. Client segment (UHNW gets more conservative thresholds)

    Design note: The routing logic here is explicit and version-controlled.
    Changes to thresholds require a code review and deployment — intentionally
    creating an audit trail for any change to routing behaviour.
    """

    # Sanctions always escalate regardless of current risk rating
    ALWAYS_ESCALATE_EVENT_TYPES = {EventType.SANCTIONS_ALERT}

    # Severity-to-action mapping by client segment
    ACTION_MATRIX: dict[str, dict[RiskSignalSeverity, ReviewAction]] = {
        "UHNW": {
            RiskSignalSeverity.LOW: ReviewAction.NO_ACTION,
            RiskSignalSeverity.MEDIUM: ReviewAction.COMPLIANCE_REVIEW,
            RiskSignalSeverity.HIGH: ReviewAction.COMPLIANCE_REVIEW,
            RiskSignalSeverity.CRITICAL: ReviewAction.IMMEDIATE_ESCALATION,
        },
        "HNW": {
            RiskSignalSeverity.LOW: ReviewAction.NO_ACTION,
            RiskSignalSeverity.MEDIUM: ReviewAction.SELF_SERVE_REFRESH,
            RiskSignalSeverity.HIGH: ReviewAction.COMPLIANCE_REVIEW,
            RiskSignalSeverity.CRITICAL: ReviewAction.IMMEDIATE_ESCALATION,
        },
        "DEFAULT": {
            RiskSignalSeverity.LOW: ReviewAction.NO_ACTION,
            RiskSignalSeverity.MEDIUM: ReviewAction.SELF_SERVE_REFRESH,
            RiskSignalSeverity.HIGH: ReviewAction.COMPLIANCE_REVIEW,
            RiskSignalSeverity.CRITICAL: ReviewAction.IMMEDIATE_ESCALATION,
        },
    }

    def calculate(self, event: PKYCDomainEvent, client_state: ClientKYCState) -> RiskDelta:
        """
        Calculate risk delta and determine routing action.
        """
        # Sanctions always escalate — non-negotiable
        if event.event_type in self.ALWAYS_ESCALATE_EVENT_TYPES:
            return RiskDelta(
                current_risk_rating=client_state.current_risk_rating,
                new_risk_indicator="SANCTIONS_HIT",
                delta_magnitude=1.0,
                requires_review=True,
                recommended_action=ReviewAction.IMMEDIATE_ESCALATION,
                delta_reasoning=(
                    f"Sanctions alert received from {event.source_system}. "
                    "Immediate escalation mandatory regardless of current risk rating."
                ),
            )

        # Determine action from segment-specific matrix
        segment_matrix = self.ACTION_MATRIX.get(
            client_state.client_segment, self.ACTION_MATRIX["DEFAULT"]
        )
        action = segment_matrix.get(event.risk_signal.severity, ReviewAction.NO_ACTION)

        # Calculate delta magnitude
        severity_scores = {
            RiskSignalSeverity.LOW: 0.15,
            RiskSignalSeverity.MEDIUM: 0.40,
            RiskSignalSeverity.HIGH: 0.70,
            RiskSignalSeverity.CRITICAL: 1.0,
        }
        delta_magnitude = severity_scores.get(event.risk_signal.severity, 0.0)

        # Amplify delta if current rating is LOW and signal is HIGH — greater departure
        if client_state.current_risk_rating == "LOW" and event.risk_signal.severity == RiskSignalSeverity.HIGH:
            delta_magnitude = min(delta_magnitude * 1.3, 1.0)
            if action == ReviewAction.SELF_SERVE_REFRESH:
                action = ReviewAction.COMPLIANCE_REVIEW  # Upgrade action

        return RiskDelta(
            current_risk_rating=client_state.current_risk_rating,
            new_risk_indicator=event.risk_signal.signal_type,
            delta_magnitude=delta_magnitude,
            requires_review=action not in (ReviewAction.NO_ACTION,),
            recommended_action=action,
            delta_reasoning=(
                f"Event type={event.event_type.value}, "
                f"signal severity={event.risk_signal.severity.value}, "
                f"client segment={client_state.client_segment}, "
                f"current rating={client_state.current_risk_rating}. "
                f"Action matrix produced: {action.value}"
            ),
        )


# ---------------------------------------------------------------------------
# Ports (Interfaces) — Hexagonal Architecture
# ---------------------------------------------------------------------------

class ClientStateRepository(Protocol):
    """
    Port for client KYC state retrieval.
    Production adapter: Azure SQL (materialised projection).
    Test adapter: in-memory dictionary.
    """
    async def get_by_client_id(self, client_id: str) -> ClientKYCState | None: ...
    async def update_risk_rating(self, client_id: str, new_rating: str, reason: str) -> None: ...


class EventStore(Protocol):
    """
    Port for append-only event persistence.
    Production adapter: Azure Cosmos DB with change feed.
    Events are immutable — no update or delete operations.
    """
    async def append(self, event: PKYCDomainEvent, delta: RiskDelta) -> None: ...


class CaseManagementPort(Protocol):
    """
    Port for Pega CLM case creation.
    Pega is retained for case workflow, SLA tracking, and four-eyes approval.
    All other CLM capabilities have been migrated to purpose-built microservices.
    """
    async def create_review_case(
        self, event: PKYCDomainEvent, delta: RiskDelta, client_state: ClientKYCState
    ) -> str: ...  # Returns case_id

    async def restrict_account(self, client_id: str, reason: str) -> None: ...


class NotificationPort(Protocol):
    """
    Port for RM and compliance officer notifications.
    Production: Azure Service Bus → notification microservice → RM tablet app / email.
    """
    async def notify_rm(self, client_id: str, review_case_id: str, message: str) -> None: ...
    async def notify_compliance(self, client_id: str, escalation_reason: str) -> None: ...


# ---------------------------------------------------------------------------
# Event Processor (Application Service)
# ---------------------------------------------------------------------------

class PKYCEventProcessor:
    """
    Core application service: processes pKYC domain events.

    Receives events from Azure Event Grid (via Kafka adapter in production),
    calculates risk delta, determines routing action, and dispatches
    to downstream services via ports.

    Design note: This class depends only on ports (interfaces).
    All infrastructure adapters are injected. The business logic here
    is fully testable without Azure, Pega, or any external service.
    This is the hexagonal architecture principle applied.

    Clean architecture compliance:
    - No Azure SDK imports in this file
    - No Pega client imports in this file
    - Infrastructure concerns are in the adapters layer
    """

    def __init__(
        self,
        client_repo: ClientStateRepository,
        event_store: EventStore,
        case_management: CaseManagementPort,
        notifications: NotificationPort,
        risk_calculator: RiskDeltaCalculator | None = None,
    ):
        self._client_repo = client_repo
        self._event_store = event_store
        self._case_management = case_management
        self._notifications = notifications
        self._risk_calculator = risk_calculator or RiskDeltaCalculator()

    async def process(self, event: PKYCDomainEvent) -> dict[str, Any]:
        """
        Process a single pKYC domain event.

        Returns a processing result dict with the action taken and case ID
        (if a case was created). This result is published back to the
        event stream as a ProcessingCompleted event.
        """
        logger.info(
            "Processing event: event_id=%s event_type=%s client_id=%s",
            event.event_id, event.event_type.value, event.client_id
        )

        # Step 1: Load current client state (materialised projection)
        client_state = await self._client_repo.get_by_client_id(event.client_id)
        if client_state is None:
            logger.warning("Client not found: client_id=%s", event.client_id)
            return {"status": "CLIENT_NOT_FOUND", "event_id": event.event_id}

        # Step 2: Calculate risk delta
        delta = self._risk_calculator.calculate(event, client_state)
        logger.info(
            "Risk delta calculated: action=%s delta_magnitude=%.2f client_id=%s",
            delta.recommended_action.value, delta.delta_magnitude, event.client_id
        )

        # Step 3: Persist event to append-only store (always — regardless of action)
        # FCA SYSC 3.2: every risk signal must be recorded with timestamp
        await self._event_store.append(event, delta)

        # Step 4: Execute routing action
        result = await self._execute_action(event, delta, client_state)

        return {
            "status": "PROCESSED",
            "event_id": event.event_id,
            "client_id": event.client_id,
            "action_taken": delta.recommended_action.value,
            "delta_magnitude": delta.delta_magnitude,
            **result,
        }

    async def _execute_action(
        self,
        event: PKYCDomainEvent,
        delta: RiskDelta,
        client_state: ClientKYCState,
    ) -> dict[str, Any]:
        """
        Execute the routing action determined by the risk delta calculator.

        Action routing:
        NO_ACTION:            Auto-update client record. No human touchpoint.
        SELF_SERVE_REFRESH:   Send targeted document request to client self-serve portal.
        COMPLIANCE_REVIEW:    Create Pega CLM case. Route to compliance officer queue.
        IMMEDIATE_ESCALATION: Restrict account. Create urgent Pega case.
                              Notify senior compliance. SLA: 24 hours.
        """
        action = delta.recommended_action

        if action == ReviewAction.NO_ACTION:
            # Auto-update — update the materialised projection only
            # The event is already in the event store (step 3 above)
            await self._client_repo.update_risk_rating(
                event.client_id,
                delta.new_risk_indicator,
                delta.delta_reasoning,
            )
            logger.info("Auto-update applied for client_id=%s", event.client_id)
            return {"case_id": None, "account_restricted": False}

        if action == ReviewAction.SELF_SERVE_REFRESH:
            # Create a targeted case in Pega with self-serve document request
            case_id = await self._case_management.create_review_case(event, delta, client_state)
            await self._notifications.notify_rm(
                event.client_id,
                case_id,
                f"Targeted KYC refresh required: {event.risk_signal.description}",
            )
            logger.info("Self-serve refresh triggered: case_id=%s client_id=%s", case_id, event.client_id)
            return {"case_id": case_id, "account_restricted": False}

        if action == ReviewAction.COMPLIANCE_REVIEW:
            # Full compliance officer review case
            case_id = await self._case_management.create_review_case(event, delta, client_state)
            await self._notifications.notify_compliance(
                event.client_id,
                f"Full KYC review required: {delta.delta_reasoning}",
            )
            logger.info("Compliance review case created: case_id=%s client_id=%s", case_id, event.client_id)
            return {"case_id": case_id, "account_restricted": False}

        if action == ReviewAction.IMMEDIATE_ESCALATION:
            # Sanctions / critical risk: restrict account first, then create case
            await self._case_management.restrict_account(
                event.client_id, event.risk_signal.description
            )
            case_id = await self._case_management.create_review_case(event, delta, client_state)
            await self._notifications.notify_compliance(
                event.client_id,
                f"IMMEDIATE ESCALATION — account restricted: {event.risk_signal.description}",
            )
            logger.critical(
                "Account restricted and escalation case created: case_id=%s client_id=%s",
                case_id, event.client_id
            )
            return {"case_id": case_id, "account_restricted": True}

        return {"case_id": None, "account_restricted": False}


# ---------------------------------------------------------------------------
# Azure Event Grid Consumer Adapter (Infrastructure Layer)
# ---------------------------------------------------------------------------

class AzureEventGridConsumerAdapter:
    """
    Infrastructure adapter: consumes events from Azure Event Grid.

    This adapter lives in the infrastructure layer, not the application layer.
    The PKYCEventProcessor has no knowledge of Azure Event Grid.

    In production: Azure Event Grid → Azure Service Bus → this consumer.
    Schema validation on ingestion, dead letter queue for malformed events.
    Idempotency key: event_id. Duplicate events are detected and skipped.

    Deployment: AKS pod with horizontal pod autoscaler based on queue depth.
    """

    def __init__(self, processor: PKYCEventProcessor):
        self._processor = processor
        self._processed_event_ids: set[str] = set()  # In production: Redis for distributed dedup

    async def handle_event_grid_message(self, raw_message: dict) -> dict:
        """
        Parse an Azure Event Grid message and route to the processor.

        Azure Event Grid wraps events in a standard envelope.
        We extract the domain event from the 'data' field.
        """
        # Idempotency check — Event Grid guarantees at-least-once delivery
        event_id = raw_message.get("id")
        if event_id in self._processed_event_ids:
            logger.warning("Duplicate event ignored: event_id=%s", event_id)
            return {"status": "DUPLICATE_IGNORED", "event_id": event_id}

        # Parse domain event from Event Grid envelope
        try:
            domain_event = self._parse_event(raw_message)
        except (KeyError, ValueError) as e:
            logger.error("Failed to parse event: event_id=%s error=%s", event_id, e)
            # In production: route to dead letter queue
            return {"status": "PARSE_ERROR", "event_id": event_id, "error": str(e)}

        # Process
        result = await self._processor.process(domain_event)

        # Mark as processed (idempotency)
        self._processed_event_ids.add(event_id)

        return result

    def _parse_event(self, raw_message: dict) -> PKYCDomainEvent:
        """
        Parse Azure Event Grid envelope into a PKYCDomainEvent.

        Event Grid schema: https://docs.microsoft.com/en-us/azure/event-grid/event-schema
        """
        data = raw_message.get("data", {})
        signal_data = data.get("risk_signal", {})

        risk_signal = RiskSignal(
            signal_type=signal_data.get("type", "UNKNOWN"),
            description=signal_data.get("description", ""),
            severity=RiskSignalSeverity(signal_data.get("severity", "MEDIUM")),
            confidence=float(signal_data.get("confidence", 0.0)),
            source_system=raw_message.get("source", ""),
        )

        return PKYCDomainEvent(
            event_id=raw_message["id"],
            event_type=EventType(raw_message["eventType"]),
            event_version=data.get("event_version", "1.0"),
            timestamp=raw_message.get("eventTime", datetime.now(timezone.utc).isoformat()),
            source_system=raw_message.get("source", ""),
            client_id=data["client_id"],
            client_segment=data.get("client_segment", "DEFAULT"),
            correlation_id=data.get("correlation_id", str(uuid.uuid4())),
            risk_signal=risk_signal,
            producer_service=data.get("producer_service", ""),
        )


# ---------------------------------------------------------------------------
# Stub Adapters for Testing
# ---------------------------------------------------------------------------

class InMemoryClientStateRepository:
    """In-memory stub for unit testing without Azure SQL."""

    def __init__(self, clients: dict[str, ClientKYCState]):
        self._clients = clients

    async def get_by_client_id(self, client_id: str) -> ClientKYCState | None:
        return self._clients.get(client_id)

    async def update_risk_rating(self, client_id: str, new_rating: str, reason: str) -> None:
        if client_id in self._clients:
            self._clients[client_id].current_risk_rating = new_rating
            logger.debug("Risk rating updated: client_id=%s new_rating=%s", client_id, new_rating)


class InMemoryEventStore:
    """In-memory stub for unit testing."""

    def __init__(self):
        self.events: list[tuple[PKYCDomainEvent, RiskDelta]] = []

    async def append(self, event: PKYCDomainEvent, delta: RiskDelta) -> None:
        self.events.append((event, delta))
        logger.debug("Event appended to store: event_id=%s", event.event_id)


class StubCaseManagement:
    """Stub Pega CLM adapter for testing."""

    def __init__(self):
        self.cases_created: list[dict] = []
        self.restricted_accounts: list[str] = []

    async def create_review_case(
        self, event: PKYCDomainEvent, delta: RiskDelta, client_state: ClientKYCState
    ) -> str:
        case_id = f"CASE-{str(uuid.uuid4())[:8].upper()}"
        self.cases_created.append({"case_id": case_id, "client_id": event.client_id})
        return case_id

    async def restrict_account(self, client_id: str, reason: str) -> None:
        self.restricted_accounts.append(client_id)


class StubNotifications:
    """Stub notification adapter for testing."""

    def __init__(self):
        self.rm_notifications: list[dict] = []
        self.compliance_notifications: list[dict] = []

    async def notify_rm(self, client_id: str, review_case_id: str, message: str) -> None:
        self.rm_notifications.append({"client_id": client_id, "case_id": review_case_id})

    async def notify_compliance(self, client_id: str, escalation_reason: str) -> None:
        self.compliance_notifications.append({"client_id": client_id, "reason": escalation_reason})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main():
    """Demonstrate pKYC event processing with stub adapters."""

    # Wire up with in-memory stubs (replace with production adapters in deployment)
    client_repo = InMemoryClientStateRepository(
        clients={
            "CLT-001": ClientKYCState(
                client_id="CLT-001",
                full_name="James Thornton",
                client_segment="UHNW",
                current_risk_rating="LOW",
                jurisdiction="GB",
            )
        }
    )
    event_store = InMemoryEventStore()
    case_mgmt = StubCaseManagement()
    notifications = StubNotifications()

    processor = PKYCEventProcessor(
        client_repo=client_repo,
        event_store=event_store,
        case_management=case_mgmt,
        notifications=notifications,
    )

    # Simulate an adverse media detection event from Factiva
    event = PKYCDomainEvent.create(
        event_type=EventType.ADVERSE_MEDIA_DETECTED,
        client_id="CLT-001",
        client_segment="UHNW",
        source_system="screening.factiva.adverse-media",
        risk_signal=RiskSignal(
            signal_type="ADVERSE_MEDIA",
            description="Regulatory enforcement action reported in source country",
            severity=RiskSignalSeverity.HIGH,
            confidence=0.91,
            source_system="factiva",
        ),
        producer_service="screening-service-v2.1",
    )

    result = await processor.process(event)

    print("Processing result:")
    for key, value in result.items():
        print(f"  {key}: {value}")

    print(f"\nEvents in store: {len(event_store.events)}")
    print(f"Cases created: {len(case_mgmt.cases_created)}")
    print(f"Compliance notifications sent: {len(notifications.compliance_notifications)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
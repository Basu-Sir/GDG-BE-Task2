# Workshop Registration & Notifications Architecture

## System Overview

```mermaid
graph TB
    subgraph "Docker Compose"
        Client([Client / curl])

        subgraph "API Service :8000"
            API[FastAPI<br/>Registration API]
        end

        subgraph "Worker Service"
            W[Worker Process<br/>Poll → Claim → Deliver]
        end

        subgraph "Mock Notifier :8001"
            MN[FastAPI<br/>Mock Notification]
            ML[(Mock Ledger<br/>idempotency_key)]
        end

        subgraph "PostgreSQL :5432"
            DB[(workshops<br/>registrations<br/>notification_deliveries)]
        end
    end

    Client -->|POST /workshops<br/>POST /registrations| API
    API -->|Atomic INSERT<br/>registration + job| DB
    W -->|FOR UPDATE SKIP LOCKED<br/>claim jobs| DB
    W -->|POST /notify<br/>Idempotency-Key header| MN
    MN --> ML
    W -->|Mark DELIVERED<br/>or schedule retry| DB
    Client -->|GET /notifications/:id| API
    API -->|Read status| DB
    Client -->|GET /deliveries| MN
```

## Event Flow: Registration

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Service
    participant DB as PostgreSQL
    participant W as Worker
    participant MN as Mock Notifier

    C->>API: POST /workshops/{id}/registrations
    API->>DB: BEGIN
    API->>DB: SELECT workshop FOR UPDATE
    API->>DB: INSERT registration
    API->>DB: INSERT notification_delivery (PENDING)
    API->>DB: COMMIT
    API->>C: 201 Created (notification_status: PENDING)

    Note over W: Poll loop
    W->>DB: SELECT ... FOR UPDATE SKIP LOCKED
    W->>DB: SET PROCESSING + lease
    W->>DB: COMMIT
    W->>MN: POST /notify (Idempotency-Key)
    MN->>MN: Record in ledger
    MN->>W: 200 OK
    W->>DB: SET DELIVERED
```

## Event Flow: Cancellation

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Service
    participant DB as PostgreSQL
    participant W as Worker

    C->>API: POST /workshops/{id}/cancel
    API->>DB: BEGIN
    API->>DB: SELECT workshop FOR UPDATE
    API->>DB: SET status=CANCELLED
    API->>DB: SELECT all registrations
    API->>DB: INSERT N notification_deliveries
    API->>DB: COMMIT
    API->>C: 200 OK (jobs_queued: N)

    Note over W: Each job handled independently
    W->>DB: Claim job for member-1
    W->>DB: Claim job for member-2
    W->>DB: Claim job for member-3
```

## Retry & Recovery Flow

```mermaid
sequenceDiagram
    participant W as Worker
    participant DB as PostgreSQL
    participant MN as Mock Notifier

    W->>DB: Claim PENDING job (attempt 1)
    W->>MN: POST /notify
    MN->>W: 500 Error

    W->>DB: SET PENDING, next_attempt_at = now + 1s

    Note over W: After backoff
    W->>DB: Claim same job (attempt 2)
    W->>MN: POST /notify
    MN->>W: Timeout (no response)

    W->>DB: SET PENDING, next_attempt_at = now + 2s

    Note over W: After backoff
    W->>DB: Claim same job (attempt 3)
    W->>MN: POST /notify (same Idempotency-Key)
    MN->>MN: Duplicate key → skip insert
    MN->>W: 200 OK
    W->>DB: SET DELIVERED
```

## Data Model

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `workshops` | id, title, status (OPEN/CANCELLED), starts_at | Workshop registry |
| `registrations` | id, workshop_id, member_id (UNIQUE together) | Member enrollments |
| `notification_deliveries` | id, event_key (UNIQUE), status, attempt_count, locked_until | Transactional outbox / durable queue |
| `mock_deliveries` | idempotency_key (PK) | Mock notifier's delivery ledger |

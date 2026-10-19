# Workshop Registration & Notifications

A durable, database-backed workshop registration system with asynchronous notification delivery using the **transactional outbox pattern**.

## Stack

| Component | Technology |
|-----------|-----------|
| API | Python 3.12, FastAPI, SQLAlchemy 2.x (async) |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Worker | Standalone async process (same codebase) |
| Mock Notifier | Separate FastAPI service |
| HTTP Client | HTTPX (async) |
| Containers | Docker Compose |
| Tests | pytest + pytest-asyncio |

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for Mermaid diagrams.

```
┌─────────┐     ┌──────────┐     ┌──────────────┐     ┌──────────────┐
│  Client  │────▶│  API     │────▶│  PostgreSQL  │◀────│   Worker     │
│  (curl)  │     │  :8000   │     │  :5432       │     │  (poll loop) │
└─────────┘     └──────────┘     └──────────────┘     └──────┬───────┘
                                                             │
                                                             ▼
                                                      ┌──────────────┐
                                                      │ Mock Notifier│
                                                      │ :8001        │
                                                      └──────────────┘
```

## Prerequisites

- Docker & Docker Compose (v2)
- curl (for demo)

## Quick Start

```bash
# 1. Clone and enter the project
cd GDG_Rec

# 2. Copy environment file
cp .env.example .env

# 3. Start everything (builds, runs migrations, starts all services)
docker compose up --build -d

# 4. Verify services are running
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8001/health
```

## Migrations

Migrations run automatically via the `migrate` service in Docker Compose.
To run manually:

```bash
docker compose run --rm migrate alembic upgrade head
```

## API Reference

### Create Workshop
```bash
curl -s -X POST http://localhost:8000/workshops \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Introduction to Cloud Computing",
    "description": "A beginner-friendly workshop",
    "starts_at": "2027-01-15T10:00:00+05:30"
  }' | python -m json.tool
```

### List / Get Workshop
```bash
curl -s http://localhost:8000/workshops | python -m json.tool
curl -s http://localhost:8000/workshops/{WORKSHOP_ID} | python -m json.tool
```

### Register Member
```bash
curl -s -X POST http://localhost:8000/workshops/{WORKSHOP_ID}/registrations \
  -H "Content-Type: application/json" \
  -d '{"member_id": "member-1042"}' | python -m json.tool
```

### List Registrations
```bash
curl -s "http://localhost:8000/workshops/{WORKSHOP_ID}/registrations" | python -m json.tool
curl -s "http://localhost:8000/workshops/{WORKSHOP_ID}/registrations?member_id=member-1042" | python -m json.tool
```

### Cancel Workshop
```bash
curl -s -X POST http://localhost:8000/workshops/{WORKSHOP_ID}/cancel | python -m json.tool
```

### Check Notification Status
```bash
curl -s http://localhost:8000/notifications/{DELIVERY_ID} | python -m json.tool
```

### Inspect Mock Deliveries
```bash
curl -s http://localhost:8001/deliveries | python -m json.tool
curl -s http://localhost:8001/deliveries/{EVENT_KEY} | python -m json.tool
```

### Switch Notifier Mode (⚠ Demo Only)
```bash
# success | fail | timeout
curl -s -X POST http://localhost:8001/admin/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "fail"}' | python -m json.tool
```

---

## Demonstration Sequence

### A. Success Path

```bash
# 1. Ensure success mode
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "success"}'

# 2. Create workshop
WS=$(curl -s -X POST http://localhost:8000/workshops \
  -H "Content-Type: application/json" \
  -d '{"title": "Cloud 101", "starts_at": "2027-01-15T10:00:00+05:30"}')
WS_ID=$(echo $WS | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Workshop: $WS_ID"

# 3. Register member A — response is immediate with PENDING status
curl -s -X POST http://localhost:8000/workshops/$WS_ID/registrations \
  -H "Content-Type: application/json" \
  -d '{"member_id": "member-A"}' | python -m json.tool

# 4. Wait 3 seconds, then check — should be DELIVERED
sleep 3
curl -s "http://localhost:8000/workshops/$WS_ID/registrations?member_id=member-A" | python -m json.tool

# 5. Verify one logical delivery in mock notifier
curl -s http://localhost:8001/deliveries | python -m json.tool
```

### B. Duplicate Registration

```bash
# Register member A again → 409
curl -s -X POST http://localhost:8000/workshops/$WS_ID/registrations \
  -H "Content-Type: application/json" \
  -d '{"member_id": "member-A"}' | python -m json.tool
# No extra job or delivery created
```

### C. Failure and Recovery

```bash
# 1. Switch to fail mode
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "fail"}'

# 2. Register member B — succeeds immediately
curl -s -X POST http://localhost:8000/workshops/$WS_ID/registrations \
  -H "Content-Type: application/json" \
  -d '{"member_id": "member-B"}' | python -m json.tool

# 3. Wait — delivery stays pending/retrying
sleep 5
curl -s "http://localhost:8000/workshops/$WS_ID/registrations?member_id=member-B" | python -m json.tool

# 4. Restore success mode
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "success"}'

# 5. Wait for delivery — should become DELIVERED with attempt_count > 1
sleep 10
curl -s "http://localhost:8000/workshops/$WS_ID/registrations?member_id=member-B" | python -m json.tool
```

### D. Timeout After Acceptance

```bash
# 1. Switch to timeout mode (mock records delivery, then delays past worker timeout)
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "timeout"}'

# 2. Register member C
curl -s -X POST http://localhost:8000/workshops/$WS_ID/registrations \
  -H "Content-Type: application/json" \
  -d '{"member_id": "member-C"}' | python -m json.tool

# 3. Worker retries — but mock ledger already has the key
sleep 15

# 4. Switch back to success so retries resolve
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "success"}'
sleep 5

# 5. Only ONE logical delivery exists for member-C
curl -s http://localhost:8001/deliveries | python -m json.tool
```

### E. Restart Recovery

```bash
# 1. Switch to fail mode and register
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "fail"}'

WS2=$(curl -s -X POST http://localhost:8000/workshops \
  -H "Content-Type: application/json" \
  -d '{"title": "Restart Test", "starts_at": "2027-02-01T10:00:00+05:30"}')
WS2_ID=$(echo $WS2 | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST http://localhost:8000/workshops/$WS2_ID/registrations \
  -H "Content-Type: application/json" \
  -d '{"member_id": "member-restart"}'

# 2. Restart API and worker (DB volume preserved)
docker compose restart api worker

# 3. Switch to success
curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "success"}'

# 4. Wait and verify delivery
sleep 10
curl -s "http://localhost:8000/workshops/$WS2_ID/registrations?member_id=member-restart" | python -m json.tool
```

### F. Cancellation

```bash
# 1. Create workshop and register 3 members
WS3=$(curl -s -X POST http://localhost:8000/workshops \
  -H "Content-Type: application/json" \
  -d '{"title": "Cancel Demo", "starts_at": "2027-03-01T10:00:00+05:30"}')
WS3_ID=$(echo $WS3 | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s -X POST http://localhost:8001/admin/mode -H "Content-Type: application/json" -d '{"mode": "success"}'

for M in member-X member-Y member-Z; do
  curl -s -X POST http://localhost:8000/workshops/$WS3_ID/registrations \
    -H "Content-Type: application/json" \
    -d "{\"member_id\": \"$M\"}"
done
sleep 3

# 2. Cancel the workshop → 3 cancellation jobs queued
curl -s -X POST http://localhost:8000/workshops/$WS3_ID/cancel | python -m json.tool

# 3. Verify deliveries in mock notifier
sleep 5
curl -s http://localhost:8001/deliveries | python -m json.tool
```

---

## Running Tests

```bash
# Ensure services are running
docker compose up --build -d

# Run tests from the host (with services accessible at localhost)
pip install -r requirements.txt
pytest tests/ -v

# Or run inside Docker
docker compose run --rm api pytest tests/ -v
```

## Delivery Semantics

This system provides **at-least-once request attempts** with **idempotent logical delivery** in the mock service.

- The worker retries failed HTTP calls with the **same idempotency key**.
- The mock notifier's `mock_deliveries` table has a unique constraint on `idempotency_key`.
- Duplicate keys return success without creating a second logical delivery.
- We do **not** claim "exactly once over HTTP" — network ambiguity prevents that without receiver cooperation.

## Limitations

- Single worker instance (though `SKIP LOCKED` supports multiple)
- No capacity limits (P2 feature)
- No pagination on list endpoints
- `member_id` is an opaque identifier, not production authentication
- No dead-letter queue — jobs retry indefinitely with capped backoff
- Mock notifier, not real email/SMS

## Key Files

| File | Purpose |
|------|---------|
| `app/models.py` | SQLAlchemy ORM models |
| `app/services/registration.py` | Atomic registration + job logic |
| `app/worker.py` | Notification delivery worker |
| `mock_notifier/main.py` | Mock notification service |
| `compose.yaml` | Docker Compose orchestration |
| `migrations/versions/0001_initial_schema.py` | Database schema |
| `DECISIONS.md` | Design rationale |

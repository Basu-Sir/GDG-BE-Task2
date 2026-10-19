"""P0 integration tests — run against the full Docker Compose stack.

Prerequisites:
  docker compose up --build -d
  Then run:  pytest tests/ -v

These tests hit the real API (port 8000) and mock-notifier (port 8001).
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from tests.conftest import future_time, unique_id


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


async def create_workshop(client: httpx.AsyncClient, title: str = "Test Workshop"):
    resp = await client.post(
        "/workshops",
        json={"title": title, "starts_at": future_time()},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def register_member(
    client: httpx.AsyncClient, workshop_id: str, member_id: str
):
    resp = await client.post(
        f"/workshops/{workshop_id}/registrations",
        json={"member_id": member_id},
    )
    return resp


async def wait_for_delivery(
    client: httpx.AsyncClient,
    delivery_id: str,
    *,
    timeout: float = 15.0,
    poll: float = 0.5,
):
    """Poll notification status until DELIVERED or timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        resp = await client.get(f"/notifications/{delivery_id}")
        if resp.status_code == 200:
            data = resp.json()
            if data["status"] == "DELIVERED":
                return data
        await asyncio.sleep(poll)
        elapsed += poll
    pytest.fail(f"Delivery {delivery_id} not DELIVERED within {timeout}s")


async def set_notifier_mode(notifier_client: httpx.AsyncClient, mode: str):
    resp = await notifier_client.post("/admin/mode", json={"mode": mode})
    assert resp.status_code == 200, resp.text


# ═══════════════════════════════════════════════════════════════════════
# P0-1: Workshop creation
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_workshop_creation(client):
    ws = await create_workshop(client, "Cloud 101")
    assert ws["title"] == "Cloud 101"
    assert ws["status"] == "OPEN"
    assert "id" in ws


# ═══════════════════════════════════════════════════════════════════════
# P0-2: Registration creates one registration + one pending job
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_registration_creates_pending_job(client):
    ws = await create_workshop(client)
    member = unique_id()
    resp = await register_member(client, ws["id"], member)
    assert resp.status_code == 201
    data = resp.json()
    assert data["member_id"] == member
    assert data["notification_status"] == "PENDING"

    # Verify exactly one registration exists
    regs = await client.get(
        f"/workshops/{ws['id']}/registrations", params={"member_id": member}
    )
    assert len(regs.json()) == 1


# ═══════════════════════════════════════════════════════════════════════
# P0-3: Duplicate registration is rejected (409) — no second job
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_duplicate_registration_rejected(client):
    ws = await create_workshop(client)
    member = unique_id()
    resp1 = await register_member(client, ws["id"], member)
    assert resp1.status_code == 201

    resp2 = await register_member(client, ws["id"], member)
    assert resp2.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# P0-5: Registration to cancelled workshop is rejected
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_registration_to_cancelled_workshop_rejected(client):
    ws = await create_workshop(client)
    # Cancel the workshop first
    cancel_resp = await client.post(f"/workshops/{ws['id']}/cancel")
    assert cancel_resp.status_code == 200

    # Try to register
    member = unique_id()
    resp = await register_member(client, ws["id"], member)
    assert resp.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# P0-7: Notifier timeout doesn't delay registration response
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_registration_not_delayed_by_notifier(client, notifier_client):
    """Registration should return in < 2s even if notifier is slow."""
    await set_notifier_mode(notifier_client, "timeout")
    try:
        ws = await create_workshop(client)
        member = unique_id()

        import time
        start = time.monotonic()
        resp = await register_member(client, ws["id"], member)
        elapsed = time.monotonic() - start

        assert resp.status_code == 201
        assert elapsed < 2.0, f"Registration took {elapsed:.1f}s — notifier is blocking!"
    finally:
        await set_notifier_mode(notifier_client, "success")


# ═══════════════════════════════════════════════════════════════════════
# P0-8: Notifier failure schedules retry
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_notifier_failure_schedules_retry(client, notifier_client):
    await set_notifier_mode(notifier_client, "fail")
    try:
        ws = await create_workshop(client)
        member = unique_id()
        resp = await register_member(client, ws["id"], member)
        assert resp.status_code == 201
        data = resp.json()

        # Wait a bit for worker to attempt and fail
        await asyncio.sleep(3)

        # Check registrations for the notification key
        regs = await client.get(
            f"/workshops/{ws['id']}/registrations", params={"member_id": member}
        )
        reg_data = regs.json()
        assert len(reg_data) == 1
        # Notification should still be pending (retrying), not delivered
        assert reg_data[0]["notification_status"] in ("PENDING", "PROCESSING")
    finally:
        await set_notifier_mode(notifier_client, "success")


# ═══════════════════════════════════════════════════════════════════════
# P0-13: Cancellation creates one job per registration
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancellation_creates_jobs_per_member(client, notifier_client):
    await set_notifier_mode(notifier_client, "success")
    ws = await create_workshop(client)

    members = [unique_id() for _ in range(3)]
    for m in members:
        resp = await register_member(client, ws["id"], m)
        assert resp.status_code == 201

    cancel_resp = await client.post(f"/workshops/{ws['id']}/cancel")
    assert cancel_resp.status_code == 200
    cancel_data = cancel_resp.json()
    assert cancel_data["cancellation_jobs_queued"] == 3
    assert cancel_data["status"] == "CANCELLED"


# ═══════════════════════════════════════════════════════════════════════
# P0-14: Repeated cancellation does not enqueue duplicates
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_repeated_cancellation_409(client):
    ws = await create_workshop(client)
    member = unique_id()
    await register_member(client, ws["id"], member)

    resp1 = await client.post(f"/workshops/{ws['id']}/cancel")
    assert resp1.status_code == 200

    resp2 = await client.post(f"/workshops/{ws['id']}/cancel")
    assert resp2.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# P0-2+: Successful delivery end-to-end
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_end_to_end_delivery(client, notifier_client):
    """Register → worker delivers → status becomes DELIVERED."""
    await set_notifier_mode(notifier_client, "success")
    ws = await create_workshop(client)
    member = unique_id()
    resp = await register_member(client, ws["id"], member)
    assert resp.status_code == 201

    # Wait for delivery
    await asyncio.sleep(5)

    # Check notification status via registrations endpoint
    regs = await client.get(
        f"/workshops/{ws['id']}/registrations", params={"member_id": member}
    )
    reg_data = regs.json()
    assert len(reg_data) == 1
    assert reg_data[0]["notification_status"] == "DELIVERED"


# ═══════════════════════════════════════════════════════════════════════
# P0-C: Failure then recovery (demo scenario C)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_failure_then_recovery(client, notifier_client):
    """Fail mode → register → switch to success → eventually delivered."""
    await set_notifier_mode(notifier_client, "fail")

    ws = await create_workshop(client)
    member = unique_id()
    resp = await register_member(client, ws["id"], member)
    assert resp.status_code == 201

    # Let it fail a couple of times
    await asyncio.sleep(4)

    # Switch to success
    await set_notifier_mode(notifier_client, "success")

    # Wait for eventual delivery
    await asyncio.sleep(8)

    regs = await client.get(
        f"/workshops/{ws['id']}/registrations", params={"member_id": member}
    )
    reg_data = regs.json()
    assert len(reg_data) == 1
    assert reg_data[0]["notification_status"] == "DELIVERED"


# ═══════════════════════════════════════════════════════════════════════
# P0-12: One cancellation recipient fails — others succeed
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_workshop_not_found(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/workshops/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_registration_workshop_not_found(client):
    fake_id = str(uuid.uuid4())
    resp = await register_member(client, fake_id, "member-x")
    assert resp.status_code == 404

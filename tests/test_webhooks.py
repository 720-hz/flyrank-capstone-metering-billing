"""Probe 4's exact scenario: a forged signature is rejected, and the same
real event delivered twice is only ever applied once. Signature
verification is tested against a webhook secret this test controls (via
monkeypatch), signed locally with the same HMAC-SHA256 scheme Stripe uses
— no real Stripe account or network call needed for any of this."""
import json

import pytest

import app.lib.stripe_client as stripe_client
from app.lib.stripe_client import WebhookVerificationError, verify_and_parse_event
from app.lib.webhooks import process_event
from tests.conftest import make_tenant
from tests.stripe_helpers import sign_payload

TEST_SECRET = "whsec_test_only_secret_for_this_suite"


@pytest.fixture(autouse=True)
def _patch_webhook_secret(monkeypatch):
    monkeypatch.setattr(stripe_client, "STRIPE_WEBHOOK_SECRET", TEST_SECRET)


def _fake_checkout_completed_payload(tenant_id: str, event_id="evt_test_1", customer="cus_test_1", subscription="sub_test_1"):
    return json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "object": "checkout.session",
                    "client_reference_id": tenant_id,
                    "metadata": {"tenant_id": tenant_id},
                    "customer": customer,
                    "subscription": subscription,
                }
            },
        }
    ).encode()


def test_valid_signature_verifies():
    payload = _fake_checkout_completed_payload("tn_x")
    header = sign_payload(payload, TEST_SECRET)
    event = verify_and_parse_event(payload, header)
    assert event["id"] == "evt_test_1"


def test_forged_signature_is_rejected():
    payload = _fake_checkout_completed_payload("tn_x")
    header = sign_payload(payload, "wrong-secret-entirely")
    with pytest.raises(WebhookVerificationError):
        verify_and_parse_event(payload, header)


def test_missing_signature_header_is_rejected():
    payload = _fake_checkout_completed_payload("tn_x")
    with pytest.raises(WebhookVerificationError):
        verify_and_parse_event(payload, "")


def test_checkout_completed_flips_tenant_free_to_pro(conn):
    tenant_id = make_tenant(conn, plan_id="free")
    event = json.loads(_fake_checkout_completed_payload(tenant_id))

    result = process_event(conn, event)
    assert result["status"] == "processed"

    tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    assert tenant["plan_id"] == "pro"
    assert tenant["subscription_status"] == "active"
    assert tenant["stripe_customer_id"] == "cus_test_1"


def test_same_event_id_processed_twice_only_applies_once(conn):
    tenant_id = make_tenant(conn, plan_id="free")
    event = json.loads(_fake_checkout_completed_payload(tenant_id))

    first = process_event(conn, event)
    second = process_event(conn, event)  # exact same event id — a Stripe redelivery

    assert first["status"] == "processed"
    assert second["status"] == "duplicate"

    count = conn.execute("SELECT COUNT(*) AS c FROM webhook_events WHERE stripe_event_id=?", (event["id"],)).fetchone()["c"]
    assert count == 1


def test_subscription_deleted_downgrades_to_free(conn):
    tenant_id = make_tenant(conn, plan_id="pro")
    conn.execute(
        "UPDATE tenants SET stripe_subscription_id='sub_del_1', stripe_customer_id='cus_del_1' WHERE id=?",
        (tenant_id,),
    )
    event = {
        "id": "evt_del_1",
        "type": "customer.subscription.deleted",
        "data": {"object": {"object": "subscription", "id": "sub_del_1", "customer": "cus_del_1"}},
    }

    process_event(conn, event)

    tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    assert tenant["plan_id"] == "free"
    assert tenant["subscription_status"] == "active"


def test_subscription_past_due_blocks_without_downgrading_plan(conn):
    tenant_id = make_tenant(conn, plan_id="pro")
    conn.execute(
        "UPDATE tenants SET stripe_subscription_id='sub_pd_1', stripe_customer_id='cus_pd_1' WHERE id=?",
        (tenant_id,),
    )
    event = {
        "id": "evt_pd_1",
        "type": "customer.subscription.updated",
        "data": {"object": {"object": "subscription", "id": "sub_pd_1", "customer": "cus_pd_1", "status": "past_due"}},
    }

    process_event(conn, event)

    tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    assert tenant["plan_id"] == "pro"  # still Pro — just blocked, not downgraded
    assert tenant["subscription_status"] == "past_due"

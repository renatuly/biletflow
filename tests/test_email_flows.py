from factories import (
    API,
    PASSWORD,
    account,
    activate_paid_sales,
    checkout,
    complete,
    event,
    login,
    publish,
    ticket_type,
)


def test_registration_sends_verification_and_gates_publishing(client, engine, mailbox):
    organizer = account(client, engine, "org@example.com", "organizer", verify=False)
    assert mailbox[-1].to == "org@example.com"
    assert "/verify-email?token=" in mailbox[-1].text
    event_id = event(client, organizer)
    ticket_type(client, organizer, event_id)
    blocked = client.post(f"{API}/events/{event_id}/publish", headers=organizer)
    assert blocked.status_code == 403

    mailbox.clear()
    assert (
        client.post(
            f"{API}/auth/resend-verification", json={"email": "nobody@example.com"}
        ).status_code
        == 202
    )
    assert not mailbox
    resent = client.post(f"{API}/auth/resend-verification", json={"email": "ORG@example.com"})
    assert resent.status_code == 202 and len(mailbox) == 1
    token = resent.headers["X-Demo-Verification-Token"]
    assert client.post(f"{API}/auth/verify-email", json={"token": token}).json()["email_verified"]
    assert client.post(f"{API}/auth/verify-email", json={"token": "bad.token"}).status_code == 400
    publish(client, organizer, event_id)


def test_password_reset_is_single_use_and_revokes_sessions(client, engine, mailbox):
    old_session = account(client, engine, "user@example.com")
    unknown = client.post(f"{API}/auth/forgot-password", json={"email": "ghost@example.com"})
    mailbox.clear()
    known = client.post(f"{API}/auth/forgot-password", json={"email": "user@example.com"})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert "X-Demo-Reset-Token" not in unknown.headers
    assert "/reset-password?token=" in mailbox[-1].html
    token = known.headers["X-Demo-Reset-Token"]

    body = {"token": token, "password": "brand-new-password"}
    assert client.post(f"{API}/auth/reset-password", json=body).status_code == 204
    assert mailbox[-1].subject == "Your BiletFlow password was changed"
    assert client.post(f"{API}/auth/reset-password", json=body).status_code == 400
    assert client.get(f"{API}/users/me", headers=old_session).status_code == 401
    assert (
        client.post(
            f"{API}/auth/login", json={"email": "user@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert client.get(
        f"{API}/users/me", headers=login(client, "user@example.com", "brand-new-password")
    ).is_success


def test_ticket_confirmation_refund_and_cancellation_emails(client, engine, mailbox):
    organizer = account(client, engine, "org@example.com", "organizer")
    attendee = account(client, engine, "aida@example.com")
    event_id = event(client, organizer, "Spring Concert")
    paid = ticket_type(client, organizer, event_id, "VIP", price_minor=500_000)
    activate_paid_sales(client, engine, organizer, event_id)
    publish(client, organizer, event_id)
    mailbox.clear()

    failed = checkout(client, attendee, event_id, [(paid, 1)]).json()
    assert complete(client, attendee, failed["id"], "failure").json()["status"] == "payment_failed"
    assert mailbox[-1].subject == "Payment failed: Spring Concert"

    order = checkout(client, attendee, event_id, [(paid, 2)]).json()
    assert complete(client, attendee, order["id"]).json()["status"] == "confirmed"
    owned = client.get(f"{API}/tickets", headers=attendee).json()
    assert [t["order_id"] for t in owned] == [order["id"], order["id"]]
    confirmation = mailbox[-1]
    assert confirmation.to == "aida@example.com"
    assert confirmation.subject == "Order confirmed: Spring Concert"
    pdfs = [a for a in confirmation.attachments if a.mime_type == "application/pdf"]
    qrs = [a for a in confirmation.attachments if a.content_id]
    assert len(pdfs) == len(qrs) == 2
    assert all(pdf.content.startswith(b"%PDF") for pdf in pdfs)
    assert all(f"cid:{qr.content_id}" in confirmation.html for qr in qrs)
    assert "Subtotal: 10 000 KZT" in confirmation.text
    # Completing again is idempotent and does not resend tickets.
    complete(client, attendee, order["id"])
    assert mailbox[-1] is confirmation

    resend = client.post(f"{API}/orders/{order['id']}/resend-confirmation", headers=attendee)
    assert resend.status_code == 202 and len(mailbox) == 3

    refund = client.post(
        f"{API}/orders/{order['id']}/refunds", headers=organizer, json={"reason": "Requested"}
    )
    assert refund.status_code == 201
    assert mailbox[-1].subject == "Refund completed: Spring Concert"

    second = checkout(client, attendee, event_id, [(paid, 1)]).json()
    complete(client, attendee, second["id"])
    mailbox.clear()
    assert client.post(f"{API}/events/{event_id}/cancel", headers=organizer).is_success
    assert [m.subject for m in mailbox] == ["Event cancelled: Spring Concert"]
    statuses = {t["status"] for t in client.get(f"{API}/tickets", headers=attendee).json()}
    assert statuses == {"refunded", "cancelled"}

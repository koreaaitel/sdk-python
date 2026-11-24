"""Replay the Korean 070 voice API with the real SDK and an in-memory HTTP service.

Install with `python -m pip install koreaaitel`, then run this file with Python.
Every number and credential in the example is synthetic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx

from clawops import ClawOps, WebhookVerificationError

API_ORIGIN = "https://number-replay.invalid"
ACCOUNT = "AC_example"
NUMBER = "07000000000"
DESTINATION = "01000000000"
VOICE_URL = "https://customer.example.invalid/voice"
STATUS_URL = "https://customer.example.invalid/status"
API_KEY = "offline-example-key"
SIGNING_KEY = "offline-example-signing-key"


def sign_fixture(url: str, form: dict[str, str]) -> str:
    # The fixture plays the provider and signs the public URL plus the sorted form.
    signed_value = url + "".join(key + value for key, value in sorted(form.items()))
    digest = hmac.new(SIGNING_KEY.encode(), signed_value.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_inbound_webhooks(client: ClawOps) -> None:
    voice = {
        "AccountId": ACCOUNT, "CallId": "CA_inbound", "Direction": "inbound",
        "From": DESTINATION, "To": NUMBER,
    }
    completed = {**voice, "CallStatus": "completed", "Duration": "7"}
    client.webhooks.verify(url=VOICE_URL, params=voice, signature=sign_fixture(VOICE_URL, voice),
                           signing_key=SIGNING_KEY)
    signature = sign_fixture(STATUS_URL, completed)
    client.webhooks.verify(url=STATUS_URL, params=completed, signature=signature, signing_key=SIGNING_KEY)

    try:
        altered = {**completed, "Duration": "8"}
        client.webhooks.verify(url=STATUS_URL, params=altered, signature=signature, signing_key=SIGNING_KEY)
    except WebhookVerificationError:
        return
    raise AssertionError("An altered callback must fail signature verification")


def main() -> None:
    numbers_path = f"/v1/accounts/{ACCOUNT}/numbers"
    calls_path = f"/v1/accounts/{ACCOUNT}/calls"
    call_id = "CA_outbound"
    route = {"routingType": "webhook", "webhookUrl": VOICE_URL, "webhookMethod": "POST",
             "statusCallback": STATUS_URL, "statusCallbackEvents": "completed"}
    number = {"number": NUMBER, "routingType": "webhook", "webhookUrl": None}
    provisioned = {**number, "smsSender": {"registered": False, "reason": "sender_number_limit_reached"}}
    configured = {**number, **route}
    dial = {"To": DESTINATION, "From": NUMBER, "Url": VOICE_URL, "StatusCallback": STATUS_URL,
            "StatusCallbackEvent": "completed"}
    queued = {"callId": call_id, "accountId": ACCOUNT, "status": "queued", "direction": "outbound",
              "to": DESTINATION, "from": NUMBER, "dateCreated": "2026-11-24T00:00:00Z"}
    exchanges = iter([
        ("POST", numbers_path, None, httpx.Response(201, json=provisioned)),
        ("PUT", f"{numbers_path}/{NUMBER}", route, httpx.Response(200, json=configured)),
        ("GET", numbers_path, None, httpx.Response(200, json={"data": [configured]})),
        ("POST", calls_path, dial, httpx.Response(201, json=queued)),
        ("GET", f"{calls_path}/{call_id}", None, httpx.Response(200, json=queued)),
        ("DELETE", f"{numbers_path}/{NUMBER}", None, httpx.Response(204)),
        ("GET", numbers_path, None, httpx.Response(200, json={"data": []})),
    ])

    def reply(request: httpx.Request) -> httpx.Response:
        method, path, body, response = next(exchanges)
        assert (request.method, str(request.url)) == (method, API_ORIGIN + path)
        assert request.headers.get("Authorization") == f"Bearer {API_KEY}"
        assert (json.loads(request.content) if request.content else None) == body
        return response

    http = httpx.Client(base_url=API_ORIGIN, transport=httpx.MockTransport(reply), trust_env=False)
    with ClawOps(api_key=API_KEY, account_id=ACCOUNT, base_url=API_ORIGIN,
                 http_client=http, max_retries=0) as client:
        issued = client.numbers.create()
        assert issued.number == NUMBER and issued.webhook_url is None
        assert issued.sms_sender and issued.sms_sender.registered is False

        client.numbers.update(issued.number, routing_type="webhook", webhook_url=VOICE_URL,
                              webhook_method="POST", status_callback=STATUS_URL,
                              status_callback_events="completed")
        saved, = client.numbers.list()
        assert (saved.number, saved.webhook_url, saved.status_callback) == (issued.number, VOICE_URL, STATUS_URL)
        verify_inbound_webhooks(client)

        call = client.calls.create(to=DESTINATION, from_=issued.number, url=VOICE_URL,
                                   status_callback=STATUS_URL, status_callback_event="completed")
        fetched = client.calls.get(call.call_id)
        assert (fetched.call_id, fetched.status) == (call.call_id, "queued")

        client.numbers.delete(issued.number)
        assert client.numbers.list() == []
    assert next(exchanges, None) is None
    print("Offline 070 replay passed: issue, route, signed inbound hooks, dial queue, release.")


if __name__ == "__main__":
    main()

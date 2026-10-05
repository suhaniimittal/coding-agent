# unified-webhook

Single Lambda ingestion point for every provider (Slack, GitHub, and any
provider added later), replacing the need for a bespoke Lambda per provider.

See `webhook_handler.py` for the flow and `providers/base.py` for the
`ProviderPlugin` contract new providers implement. Publishes normalised
`CanonicalEvent`s to each tenant's own `unified-events-{env}-{tenant_id}` SQS
queue (the same per-tenant queue nebula provisions for every tenant), consumed
by that tenant's pulsar pod via `ProviderEventSqsConsumer`.

Route: `/webhooks/v2/{provider_slug}` (API Gateway path param).

## Twilio (SMS / WhatsApp / RCS)

One plugin serves all three — Twilio delivers them through the same Messaging
webhook and the channel is read off the address prefix, so the emitted
`event_type` is `sms.received` / `whatsapp.received` / `rcs.received`.

Twilio is the only provider here that posts `application/x-www-form-urlencoded`
rather than JSON; `ProviderPlugin.parse_body` is the hook for that.

Secret keys (common or per-tenant, same resolution as every other provider):

| Key | Purpose |
|---|---|
| `TWILIO_AUTH_TOKEN` | Signature validation. |
| `TWILIO_WEBHOOK_URL` | The **exact** public URL configured in the Twilio console. Twilio signs over the URL, so a mismatch fails validation — this is supplied rather than reconstructed, which is why this plugin needs none of the query-ordering brute force the voice-webhook does. |
| `TWILIO_NUMBER_TENANT_MAP` | `{"+15559876543": {"tenant_id": "...", "tenant_name": "..."}}`, keyed on the bare E.164 so one entry covers SMS, WhatsApp and RCS for that number. A plain `{"+1555...": "tenant-id"}` string value also works. |

In the Twilio console point the number's **A MESSAGE COMES IN** webhook at
`POST https://<webhook-host>/webhooks/v2/twilio`.

Downstream: pulsar refuses to run an agent for a sender with no
`user_provider_mapping` row (`provider='phone'`) — a phone number is reachable by
anyone, unlike a Slack/Teams channel whose membership is the access boundary.
Carrier keywords (STOP/HELP/…) are dropped before dispatch; enable **Advanced
Opt-Out** on the Messaging Service so Twilio sends the mandated replies.

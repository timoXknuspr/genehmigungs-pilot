import os
import re
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

flask_app = Flask(__name__)
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)
handler = SlackRequestHandler(app)

PENDING_REQUESTS = {}

@app.function("request_approval")
def request_approval_function(inputs, complete, fail, client, body):
    try:
        approver_ids = inputs["approver_ids"]
        request_channel_id = inputs["request_channel_id"]
        request_text = inputs["request_text"]
        requester_id = inputs["requester_id"]

        execution_id = body.get("event", {}).get("function_execution_id")

        response = client.chat_postMessage(
            channel=request_channel_id,
            text=f"Neue Genehmigungsanfrage von <@{requester_id}>",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f"Anfrage von <@{requester_id}>:\n>{request_text}"}},
                {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Genehmigen"}, "style": "primary", "action_id": f"approve__{execution_id}"},
                    {"type": "button", "text": {"type": "plain_text", "text": "Ablehnen"}, "style": "danger", "action_id": f"deny__{execution_id}"}
                ]}
            ]
        )
        message_ts = response["ts"]

        PENDING_REQUESTS[execution_id] = {
            "requester_id": requester_id,
            "channel_id": request_channel_id,
            "message_ts": message_ts,
            "approver_ids": approver_ids
        }

        for approver_id in approver_ids:
             client.chat_postMessage(
                channel=approver_id,
                text=f"Hallo! Eine neue Anfrage in <#{request_channel_id}> wartet auf deine Entscheidung."
            )
    except Exception as e:
        fail(error=f"Fehler beim Senden der Anfrage: {e}")

@app.action(re.compile("^(approve|deny)__"))
def handle_approval_buttons(ack, body, client):
    ack()
    action_id = body["actions"][0]["action_id"]
    decision, execution_id = action_id.split("__")
    decider_id = body["user"]["id"]

    request_info = PENDING_REQUESTS.get(execution_id)
    if not request_info:
        client.chat_postEphemeral(channel=body["channel"]["id"], user=decider_id, text="Diese Anfrage wurde bereits bearbeitet oder ist veraltet.")
        return

    if decider_id not in request_info["approver_ids"]:
        client.chat_postEphemeral(channel=body["channel"]["id"], user=decider_id, text="Du bist nicht als Genehmiger für diese Anfrage eingetragen.")
        return

    PENDING_REQUESTS.pop(execution_id, None)
    outputs = {"decision": "Genehmigt", "comment": "Kein Kommentar"} if decision == "approve" else {"decision": "Abgelehnt", "comment": "Kein Kommentar"}

    try:
        client.functions_complete(execution_id=execution_id, outputs=outputs)

        original_message_blocks = body["message"]["blocks"]
        original_message_blocks.pop()
        original_message_blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Entschieden von <@{decider_id}>: *{outputs['decision']}*"}]})

        client.chat_update(channel=body["channel"]["id"], ts=body["message"]["ts"], text="Anfrage entschieden.", blocks=original_message_blocks)

        client.chat_postMessage(channel=request_info["channel_id"], thread_ts=request_info["message_ts"], text=f"Update für <@{request_info['requester_id']}>: Der Antrag wurde von <@{decider_id}> *{outputs['decision']}*.")
    except Exception as e:
        print(f"Fehler beim Abschließen des Workflows: {e}")

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
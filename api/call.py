from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from livekit.api import AccessToken, VideoGrants
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/call")
async def call_customer(phone_number: str):
    lk = api.LiveKitAPI()

    room = f"hunar-{phone_number[-6:]}"

    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=os.environ["AGENT_NAME"],
            room=room,
            metadata=phone_number,
        )
    )

    sip_trunk_id = os.environ["SIP_TRUNK_ID"]

    await lk.sip.create_sip_participant(
        api.CreateSIPParticipantRequest(
            sip_trunk_id=sip_trunk_id,
            sip_call_to=phone_number,
            room_name=room,
            participant_identity=f"sip_{uuid.uuid4().hex[:8]}",
            participant_name="Customer",
            krisp_enabled=True,
            wait_until_answered=True,
            play_dialtone=True,
        )
    )

    return {"status": "calling", "room": room}


@app.post("/web-call")
async def start_web_call():
    """Create a room, dispatch the voice agent, and return a join token for the browser."""
    api_key = os.environ["LIVEKIT_API_KEY"]
    api_secret = os.environ["LIVEKIT_API_SECRET"]
    livekit_url = os.environ["LIVEKIT_URL"]

    room_name = f"web-{uuid.uuid4().hex[:8]}"
    identity = f"user-{uuid.uuid4().hex[:6]}"

    lk = api.LiveKitAPI()
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=os.environ.get("AGENT_NAME", "Seema"),
            room=room_name,
            metadata=identity,
        )
    )

    token = AccessToken(api_key, api_secret) \
        .with_identity(identity) \
        .with_name("Web User") \
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        ))

    return {
        "token": token.to_jwt(),
        "url": livekit_url.replace("ws://", "wss://") if livekit_url.startswith("ws://") else livekit_url,
        "room": room_name,
    }

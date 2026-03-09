from fastapi import FastAPI
from livekit import api
import os
import uuid
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

@app.post("/call")
async def call_customer(phone_number: str):

    lk = api.LiveKitAPI()

    room = f"hunar-{phone_number[-6:]}"

    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=os.environ["AGENT_NAME"],
            room=room,
            metadata=phone_number
        )
    )

          # Create outbound SIP call
    sip_trunk_id = os.environ["SIP_TRUNK_ID"]
    #phone_number = os.environ["PHONE_NUMBER"]


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
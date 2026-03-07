import time
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from google.genai import types

from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    RunContext,
    RoomInputOptions,
    function_tool,
)

from livekit.agents.voice.agent_session import VoiceActivityVideoSampler
from livekit.plugins import google, silero, noise_cancellation

from prompt import (
    AGENT_INSTRUCTIONS,
    COURSE_PRICE_FULL,
    COURSE_PRICE_DISCOUNTED,
    INSTALLMENT_1,
    INSTALLMENT_2,
    INSTALLMENT_3,
)

load_dotenv()

# -------------------------
# Logging
# -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("hunar-agent")


# -------------------------
# Session State
# -------------------------

@dataclass
class SalesCallSession:
    lead_id: str = ""
    lead_name: str = ""
    phone_number: str = ""
    gender: str = "female"
    call_status: str = "new"
    interest_level: str = "unknown"

    objections_raised: list[str] = field(default_factory=list)
    products_discussed: list[str] = field(default_factory=list)

    conversation_history: list[dict[str, Any]] = field(default_factory=list)


# -------------------------
# Prompt Variable Injection
# -------------------------

def build_prompt(session: SalesCallSession) -> str:

    variables = {
        "Client_Name": session.lead_name or "आप",
        "Client_Gender": session.gender or "female",
        "Course_Price_Full": os.getenv("COURSE_PRICE_FULL", COURSE_PRICE_FULL),
        "Course_Price_Discounted": os.getenv("COURSE_PRICE_DISCOUNTED", COURSE_PRICE_DISCOUNTED),
        "Installment_1": os.getenv("INSTALLMENT_1", INSTALLMENT_1),
        "Installment_2": os.getenv("INSTALLMENT_2", INSTALLMENT_2),
        "Installment_3": os.getenv("INSTALLMENT_3", INSTALLMENT_3),
        "WhatsApp_Number_Spoken": session.phone_number or ""
    }

    instructions = AGENT_INSTRUCTIONS

    for key, value in variables.items():
        instructions = instructions.replace(f"{{{key}}}", str(value))

    logger.info(f"Client Name Injected In Prompt: {variables['Client_Name']}")

    return instructions

# -------------------------
# Guardrail Response
# -------------------------


def guardrail_response() -> str:
    return """
====================================================
SECTION — KNOWLEDGE GUARDRAIL
====================================================

You are a Hunar Online Courses voice agent.

Use the system prompt as the PRIMARY source of information for:
- Hunar courses
- Course catalog
- Fees
- Installments
- Enrollment process
- Student success stories
- Hunar platform details

RULES:

1. For Hunar-related questions:
Only answer using the information provided in this prompt.
Do NOT invent course details, prices, policies, or features.

2. For general knowledge questions:
You may answer normally using your general knowledge.
Example: history, geography, public figures, etc.

3. If the question is about Hunar but the answer is NOT in the prompt:
Do NOT guess.

Respond politely:

Hindi / Hinglish:
"यह जानकारी अभी मेरे पास उपलब्ध नहीं है। मैं आपकी query note कर लेती हूँ और हमारी team आपको सही जानकारी के साथ contact करेगी।"

English:
"I don't have that information right now. I will note your query and our team will get back to you with the correct details."

4. After answering unrelated questions, gently bring the conversation back to Hunar courses.

Example redirection:
"वैसे अगर आप चाहें तो मैं आपको हुनर के courses के बारे में बता सकती हूँ।"
"""


# -------------------------
# Agent
# -------------------------

class HunarSalesAgent(Agent):

    def __init__(self, session_data: SalesCallSession):

        instructions = build_prompt(session_data) + guardrail_response()

        super().__init__(instructions=instructions)

        self.session_data = session_data

        logger.info("HunarSalesAgent initialized")


# -------------------------
# Function Tools
# -------------------------

    @function_tool()
    async def start_sales_call(
        self,
        context: RunContext[SalesCallSession],
        lead_name: str = "",
        phone_number: str = "",
    ):

        session = context.userdata

        if lead_name:
            session.lead_name = lead_name

        if phone_number:
            session.phone_number = phone_number

        session.call_status = "contacted"

        logger.info(f"Call started with {session.lead_name}")


    @function_tool()
    async def track_interest(
        self,
        context: RunContext[SalesCallSession],
        interest_level: str
    ):

        session = context.userdata
        session.interest_level = interest_level

        logger.info(f"Interest updated → {interest_level}")


    @function_tool()
    async def log_objection(
        self,
        context: RunContext[SalesCallSession],
        objection_type: str,
        objection_details: str = ""
    ):

        session = context.userdata

        session.objections_raised.append({
            "type": objection_type,
            "details": objection_details
        })

        logger.info(f"Objection logged → {objection_type}")


    @function_tool()
    async def discuss_product(
        self,
        context: RunContext[SalesCallSession],
        product_name: str,
        category: str = "general"
    ):

        session = context.userdata

        session.products_discussed.append(product_name)

        logger.info(f"Product discussed → {product_name}")


    @function_tool()
    async def schedule_follow_up(
        self,
        context: RunContext[SalesCallSession],
        follow_up_date: str,
        follow_up_notes: str = ""
    ):

        session = context.userdata

        session.call_status = "callback_scheduled"

        logger.info(f"Follow-up scheduled → {follow_up_date}")


    @function_tool()
    async def update_call_status(
        self,
        context: RunContext[SalesCallSession],
        status: str
    ):

        session = context.userdata

        session.call_status = status

        logger.info(f"Call status → {status}")


# -------------------------
# Conversation Start
# -------------------------

    async def on_enter(self):

        logger.info("Agent on_enter triggered")

        await self.session.generate_reply(
            instructions="""
Start the conversation strictly from STEP 1 of the system prompt.

IMPORTANT GUARDRAIL:
Only answer questions using the information available in the system prompt.

If the customer asks anything outside the provided information, politely say that you do not have the information right now and that the Hunar team will follow up with the correct details.

Never guess or fabricate information.
"""
        )


# -------------------------
# Entry Point
# -------------------------

async def entrypoint(ctx: agents.JobContext):

    logger.info("Starting Hunar Sales Agent")

    lead_id = ctx.room.name if ctx.room else "test-lead"

    # CRM / Lead variables
    session_data = SalesCallSession(
        lead_id=lead_id,
        lead_name="Bhuwan",
        phone_number="9903232930",
        gender = "male"
    )

    # Voice Activity Detection
    vad = silero.VAD.load(
        min_speech_duration=0.2, # 0.4
        min_silence_duration=0.5, #0.8
        activation_threshold=0.6, #0.75
        prefix_padding_duration=0.3, #0.5
    )

    # LLM
    llm = google.realtime.RealtimeModel(
        model="gemini-live-2.5-flash",
        language="hi-IN",
        voice="Aoede",
        vertexai=True,
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False
            ),
            activity_handling = types.ActivityHandling.NO_INTERRUPTION
        )
    )

    session = AgentSession[SalesCallSession](
        userdata=session_data,
        llm=llm,
        vad=vad,
        video_sampler=VoiceActivityVideoSampler(
            speaking_fps=0.3,
            silent_fps=0.2
        ),
        user_away_timeout=20
    )

    agent = HunarSalesAgent(session_data)

    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony()
        ),
    )


# -------------------------
# Run
# -------------------------

if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint)
    )
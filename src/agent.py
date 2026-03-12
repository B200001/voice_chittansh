import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from google.genai import types

from livekit import agents, api
from livekit.rtc import RemoteAudioTrack
from livekit.agents import (
    Agent,
    AgentSession,
    RunContext,
    RoomInputOptions,
    function_tool,
    get_job_context,
)
from livekit.agents.beta.tools import EndCallTool

from livekit.agents.voice.agent_session import VoiceActivityVideoSampler

from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    CloseEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    FunctionToolsExecutedEvent,
    SpeechCreatedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
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
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("hunar-agent")
# Log everything from livekit.agents (transcripts, tool calls, etc.)
logging.getLogger("livekit.agents").setLevel(logging.DEBUG)


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

DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def phone_number_to_spoken(phone: str) -> str:
    """Convert phone number to digit-by-digit spoken form for TTS (avoids million/hundred)."""
    if not phone:
        return ""
    return " ".join(DIGIT_WORDS.get(c, c) for c in str(phone).strip() if c.isdigit())


def build_prompt(session: SalesCallSession) -> str:

    variables = {
        "Client_Name": session.lead_name or "आप",
        "Client_Gender": session.gender or "female",
        "Course_Price_Full": os.getenv("COURSE_PRICE_FULL", COURSE_PRICE_FULL),
        "Course_Price_Discounted": os.getenv("COURSE_PRICE_DISCOUNTED", COURSE_PRICE_DISCOUNTED),
        "Installment_1": os.getenv("INSTALLMENT_1", INSTALLMENT_1),
        "Installment_2": os.getenv("INSTALLMENT_2", INSTALLMENT_2),
        "Installment_3": os.getenv("INSTALLMENT_3", INSTALLMENT_3),
        "WhatsApp_Number_Spoken": phone_number_to_spoken(session.phone_number or ""),
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
Do NOT guess. Respond politely using the SPEAK lines below.

SPEAK (Hindi / Hinglish): "यह जानकारी अभी मेरे पास उपलब्ध नहीं है। मैं आपकी query note कर लेती हूँ और हमारी team आपको सही जानकारी के साथ contact करेगी।"

SPEAK (English): "I don't have that information right now. I will note your query and our team will get back to you with the correct details."

4. After answering unrelated questions, gently bring the conversation back to Hunar courses.

SPEAK (Hindi / Hinglish) for redirection: "वैसे अगर आप चाहें तो मैं आपको हुनर के courses के बारे में बता सकती हूँ।"
"""


# -------------------------
# Agent
# -------------------------

class HunarSalesAgent(Agent):

    def __init__(self, session_data: SalesCallSession):

        instructions = build_prompt(session_data) + guardrail_response()

        end_call_tool = EndCallTool(
            extra_description="Use when user says: call khatam kardo, bye, mujhe jana hai, I have to go, disconnect karo",
            end_instructions="Say a brief warm goodbye in Hindi/Hinglish (1 sentence), then end the call.",
            delete_room=True,
        )
        super().__init__(instructions=instructions, tools=end_call_tool.tools)

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

    @function_tool()
    async def update_whatsapp_number(
        self,
        context: RunContext[SalesCallSession],
        phone_number: str,
    ) -> str:
        """Validate and store the customer's WhatsApp number. India format: exactly 10 digits, must start with 6, 7, 8, or 9. Call this when the user provides their WhatsApp number. Extract only digits from their speech (e.g. 9876543210). Returns 'valid' if accepted, or an error message in Hindi/English to speak if invalid."""
        session = context.userdata
        digits = "".join(c for c in str(phone_number).strip() if c.isdigit())
        if len(digits) != 10:
            return f"INVALID: WhatsApp number exactly 10 digits hona chahiye. Aapne {len(digits)} digits diye. Kripya sahi 10-digit number bataiye."
        first = digits[0]
        if first not in "6789":
            return "INVALID: India mein WhatsApp number 6, 7, 8 ya 9 se start hona chahiye. Kripya sahi number bataiye."
        session.phone_number = digits
        logger.info(f"WhatsApp number validated and stored: {digits}")
        return "valid"

    @function_tool()
    async def cut_call(self, context: RunContext[SalesCallSession]):
        """End the call when the user says goodbye, bye, call khatam kardo, mujhe jana hai, disconnect, etc. Call this to hang up. Say a brief goodbye first, then disconnect after it plays."""
        logger.info("cut_call: user requested to end call — will disconnect after goodbye plays")
        job_ctx = None
        try:
            job_ctx = get_job_context()
            async def _delete_room_on_shutdown() -> None:
                try:
                    await job_ctx.delete_room()
                    logger.info("cut_call: delete_room completed")
                except Exception as e:
                    logger.warning("cut_call: delete_room error: %s", e)
            job_ctx.add_shutdown_callback(_delete_room_on_shutdown)
        except Exception as e:
            logger.warning("cut_call: get_job_context error: %s", e)

        # Wait for current speech (goodbye) to play out before cutting — smooth ending
        try:
            await context.wait_for_playout()
            await asyncio.sleep(2)  # Extra 2s so user hears full goodbye
        except Exception as e:
            logger.debug("cut_call: wait_for_playout: %s", e)
            await asyncio.sleep(4)  # Fallback: allow ~4s for goodbye to play

        try:
            agent_session = context.session
            agent_session.shutdown()
        except Exception as e:
            logger.warning("cut_call: session.shutdown error: %s", e)
        if job_ctx is not None:
            try:
                job_ctx.shutdown(reason="user ended call")
            except Exception as e:
                logger.warning("cut_call: job_ctx.shutdown error: %s", e)
        return "Call ended."


# -------------------------
# Inactivity / No-Response Helper
# -------------------------


async def _graceful_hangup(
    session: AgentSession[SalesCallSession],
    ctx: agents.JobContext,
) -> None:
    """Say goodbye and end the call gracefully. Used for no-response timeout."""
    try:
        await session.generate_reply(
            instructions="""
Say a warm, brief goodbye in Hindi/Hinglish. You are ending the call because the customer didn't respond.
Say something like: "मैं समझ गई, शायद आप अभी busy हैं। मैं आपको बाद में call करूँगी। धन्यवाद, अलविदा।"
Or in English: "I understand you might be busy. I'll call you back later. Thank you, goodbye."
Keep it short (1-2 sentences). Do NOT ask questions. Just say goodbye and end.
"""
        )
        await asyncio.sleep(6)  # Allow goodbye to play fully
    except Exception as e:
        logger.debug("graceful hangup generate_reply: %s", e)
    try:
        session.shutdown()
        job_ctx = get_job_context()
        async def _delete() -> None:
            try:
                if hasattr(job_ctx, "delete_room"):
                    await job_ctx.delete_room()
                else:
                    await job_ctx.api.room.delete_room(
                        api.DeleteRoomRequest(room=job_ctx.room.name)
                    )
            except Exception:
                pass
        job_ctx.add_shutdown_callback(_delete)
        job_ctx.shutdown(reason="user no response")
    except Exception:
        asyncio.get_running_loop().call_later(0.5, lambda: os._exit(0))


# -------------------------
# Conversation Start
# -------------------------

    async def on_enter(self):
        logger.info("Agent on_enter triggered")
        # First greeting is sent when remote audio track is subscribed (track_subscribed in entrypoint)


# -------------------------
# Entry Point
# -------------------------

async def entrypoint(ctx: agents.JobContext):

    logger.info("Starting Hunar Sales Agent")

    lead_id = ctx.room.name if ctx.room else "test-lead"

    # CRM / Lead variables
    #राशियाँ सिरिशा 9015688994
    session_data = SalesCallSession(
        lead_id=lead_id,
        lead_name="सिरिशा",
        phone_number="9015688994",
        gender = "female"
    )

    # Voice Activity Detection - balanced for:
    # 1. Catching short utterances ("हाँ", "फ़ैशन") so user doesn't repeat
    # 2. Not stopping agent mid-sentence from false triggers (echo, breath, cough)
    vad = silero.VAD.load(
        min_speech_duration=0.15,  # lower = catches "हाँ", "yes", "फ़ैशन" etc. (was 0.35 - too high, missed short replies)
        min_silence_duration=0.9,  # longer = less cutting user off mid-sentence, more time to finish thought
        activation_threshold=0.68,  # higher = fewer false "user speaking" when agent speaks (reduces mid-speech stops)
        prefix_padding_duration=0.35,  # capture more context at start of user speech
    )

    
    # This reduces random voice breaking from false VAD triggers
    llm = google.realtime.RealtimeModel(
        model="gemini-live-2.5-flash",
        language="hi-IN",
        voice="Aoede",
        vertexai=True,
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False,
            ),
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
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
        user_away_timeout=28,  # seconds before "away" - gives user more time, reduces premature "क्या आप सुन रहे हैं?"
        aec_warmup_duration=2   # longer AEC warmup = better echo cancellation when agent speaks, fewer mid-speech stops
    )
    logger.info("Agent session created for room: %s", ctx.room.name if ctx.room else "no-room")

    agent = HunarSalesAgent(session_data)

    inactivity_task: asyncio.Task | None = None

    async def user_presence_task() -> None:
        """Prompt 1-2 times, then gracefully end call if no response."""
        # nonlocal inactivity_task
        for attempt in range(2):  # Prompt twice before giving up
            await session.generate_reply(
                instructions=(
                    "The user has been silent. Politely check if they can hear you. "
                    "In Hindi: क्या आप सुन रहे हैं? बता दीजिए अगर आप busy हैं। "
                    "Keep it brief (1 short sentence)."
                )
            )
            await asyncio.sleep(10)
        # Still no response after 2 prompts → graceful goodbye and hangup
        logger.info("User still inactive after 2 prompts — ending call gracefully")
        await _graceful_hangup(session, ctx)

    def _on_user_state_changed(ev: UserStateChangedEvent) -> None:
        nonlocal inactivity_task
        logger.info("[USER_STATE] %s -> %s", ev.old_state, ev.new_state)
        if ev.new_state == "away":
            inactivity_task = asyncio.create_task(user_presence_task())
            return
        if inactivity_task is not None:
            inactivity_task.cancel()
            inactivity_task = None

    def _on_user_input_transcribed(ev: UserInputTranscribedEvent) -> None:
        final = " (final)" if ev.is_final else ""
        logger.info("[TRANSCRIPT] user%s: %s", final, ev.transcript or "")

    def _on_agent_state_changed(ev: AgentStateChangedEvent) -> None:
        logger.info("[AGENT_STATE] %s -> %s", ev.old_state, ev.new_state)

    def _on_conversation_item_added(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        role = getattr(item, "role", None) or getattr(item, "type", "?")
        content = getattr(item, "content", None) or getattr(item, "text", "") or str(item)
        if isinstance(content, str) and len(content) > 200:
            content = content[:200] + "..."
        logger.info("[CONV] %s: %s", role, content)

    def _on_function_tools_executed(ev: FunctionToolsExecutedEvent) -> None:
        for fc, out in ev.zipped():
            logger.info("[TOOL] %s(%s) -> %s", fc.name, fc.arguments or "", out.output if out else None)

    def _on_speech_created(ev: SpeechCreatedEvent) -> None:
        logger.info("[SPEECH] created source=%s user_initiated=%s", ev.source, ev.user_initiated)

    def _on_error(ev: ErrorEvent) -> None:
        logger.error("[ERROR] %s from %s", ev.error, type(ev.source).__name__)

    def _on_close(ev: CloseEvent) -> None:
        logger.info("[CLOSE] reason=%s error=%s", ev.reason, ev.error)

    session.on("user_state_changed", _on_user_state_changed)
    session.on("user_input_transcribed", _on_user_input_transcribed)
    session.on("agent_state_changed", _on_agent_state_changed)
    session.on("conversation_item_added", _on_conversation_item_added)
    session.on("function_tools_executed", _on_function_tools_executed)
    session.on("speech_created", _on_speech_created)
    session.on("error", _on_error)
    session.on("close", _on_close)

    first_greeting_sent = False

    def _on_track_subscribed(track, publication, participant):
        nonlocal first_greeting_sent
        if first_greeting_sent:
            return
        if not isinstance(track, RemoteAudioTrack):
            return
        first_greeting_sent = True
        ctx.room.off("track_subscribed", _on_track_subscribed)

        async def _send_first_greeting():
            await asyncio.sleep(1)
            try:
                await session.generate_reply(
                    instructions="""
Start the conversation strictly from STEP 1 of the system prompt.
IMPORTANT GUARDRAIL: Only answer questions using the information available in the system prompt.
If the customer asks anything outside the provided information, politely say that you do not have the information right now and that the Hunar team will follow up with the correct details.
Never guess or fabricate information.
"""
                )
                logger.info("First greeting sent (on track_subscribed)")
            except Exception as e:
                logger.warning("First greeting failed: %s", e)

        asyncio.create_task(_send_first_greeting())

    if ctx.room:
        ctx.room.on("track_subscribed", _on_track_subscribed)

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
        agents.WorkerOptions(entrypoint_fnc=entrypoint,
        agent_name=os.getenv("AGENT_NAME", "Seema")
        )
    )
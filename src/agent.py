import asyncio
import logging
from logging.handlers import RotatingFileHandler
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
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from prompt import (
    AGENT_INSTRUCTIONS,
    COURSE_PRICE_FULL,
    COURSE_PRICE_DISCOUNTED,
    INSTALLMENT_1,
    INSTALLMENT_2,
    INSTALLMENT_3,
)
from audio_filter import FilteredAudioInput

load_dotenv()

# -------------------------
# Logging
# -------------------------

# Create logs directory if it doesn't exist
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Configure logging with both file and console output
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        # File handler with rotation (10MB per file, keep 5 backups)
        RotatingFileHandler(
            os.path.join(log_dir, "application.log"),
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        ),
        # Console handler
        logging.StreamHandler()
    ]
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

2. For unrelated or general knowledge questions:
Do NOT answer the question content.
Politely decline in one line and redirect to Hunar course counseling.

SPEAK (Hindi / Hinglish): "माफ़ कीजिए, मैं अभी सिर्फ Hunar course की जानकारी में मदद कर सकती हूँ। क्या आप Fashion, Food या Beauty में interest बताना चाहेंगी?"

SPEAK (English): "Sorry, I can only help with Hunar course information on this call. Would you like to explore Fashion, Food, or Beauty courses?"

3. If the question is about Hunar but the answer is NOT in the prompt:
Do NOT guess. Respond politely using the SPEAK lines below.

SPEAK (Hindi / Hinglish): "यह जानकारी अभी मेरे पास उपलब्ध नहीं है। मैं आपकी query note कर लेती हूँ और हमारी team आपको सही जानकारी के साथ contact करेगी।"

SPEAK (English): "I don't have that information right now. I will note your query and our team will get back to you with the correct details."

4. If user asks out-of-scope questions repeatedly (2 times):
Move toward closure or WhatsApp follow-up.

SPEAK (Hindi / Hinglish) for redirection: "वैसे अगर आप चाहें तो मैं आपको हुनर के courses के बारे में बता सकती हूँ।"
"""


# -------------------------
# Agent
# -------------------------

class HunarSalesAgent(Agent):

    def __init__(self, session_data: SalesCallSession):

        instructions = build_prompt(session_data) + guardrail_response()

        end_call_tool = EndCallTool(
            extra_description="Use when user says: call खत्म करो, bye, मुझे जाना है, I have to go, disconnect करो",
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
        """
        Marks the beginning of a sales call and records lead information.
        
        Call this function when you start engaging with the customer after the initial greeting.
        This helps track that the sales conversation has officially begun.
        
        Args:
            lead_name: The customer's name (optional)
            phone_number: The customer's phone number (optional)
        """
        logger.info(f"[FUNCTION_CALL] start_sales_call(lead_name='{lead_name}', phone_number='{phone_number}')")
        
        session = context.userdata

        if lead_name:
            session.lead_name = lead_name

        if phone_number:
            session.phone_number = phone_number

        session.call_status = "contacted"

        logger.info(f"Call started with {session.lead_name}")
        return "Call started. Continue to STEP 3, if not already"


    @function_tool()
    async def track_interest(
        self,
        context: RunContext[SalesCallSession],
        interest_level: str
    ):
        """
        Tracks the customer's level of interest in the courses or products.
        
        Use this to record how interested the customer seems based on their responses.
        This helps qualify the lead and adjust your approach accordingly.
        
        Args:
            interest_level: The level of interest - "high", "medium", "low", or "not_interested"
        """
        logger.info(f"[FUNCTION_CALL] track_interest(interest_level='{interest_level}')")
        
        session = context.userdata
        session.interest_level = interest_level

        logger.info(f"Interest updated → {interest_level}")
        return "Interest updated. Continue to STEP 5, if not already" 


    @function_tool()
    async def log_objection(
        self,
        context: RunContext[SalesCallSession],
        objection_type: str,
        objection_details: str = ""
    ):
        """
        Records customer objections during the sales call.
        
        Use this when the customer raises concerns or objections about the course, price,
        time commitment, or any other aspect. This helps track common objections and
        how to address them.
        
        Args:
            objection_type: Type of objection - "price", "time", "quality", "trust", "family", etc.
            objection_details: Additional details about the specific objection (optional)
        """
        logger.info(f"[FUNCTION_CALL] log_objection(objection_type='{objection_type}', objection_details='{objection_details}')")
        
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
        """
        Records the specific sub-category the customer chose in STEP 5.
        
        CRITICAL TIMING: Only call this ONCE in STEP 5 after asking the sub-category question.
        
        WHEN TO CALL:
        - In STEP 5, AFTER you ask: "Fashion में आपका interest किसमें है? Garment making, embroidery..."
        - User responds with ONE sub-category: "embroidery" or "garment making" or "baking" etc.
        - You acknowledge: "ठीक है, [sub-category]।"
        - THEN call this tool with that sub-category name
        
        WHEN NOT TO CALL:
        - STEP 3: User says "Fashion" or "Food" or "Beauty" → DO NOT CALL
        - STEP 4: User says "hobby" or "job" or "earning" → DO NOT CALL
        - STEP 6-15: Any other step → DO NOT CALL
        - Do NOT call multiple times or predict what user might want
        
        Args:
            product_name: The exact sub-category user mentioned (e.g., "embroidery", "garment making", "baking", "bridal makeup")
            category: The main category - "fashion", "food", or "beauty"
        
        Example:
            User says "embroidery" in STEP 5 → discuss_product("embroidery", "fashion")
        """
        logger.info(f"[FUNCTION_CALL] discuss_product(product_name='{product_name}', category='{category}')")
        
        session = context.userdata

        session.products_discussed.append(product_name)

        logger.info(f"Product discussed → {product_name}")
        return ""


    @function_tool()
    async def schedule_follow_up(
        self,
        context: RunContext[SalesCallSession],
        follow_up_date: str,
        follow_up_notes: str = ""
    ):
        """
        Schedules a follow-up call with the customer.
        
        Use this when the customer requests a callback or wants to think about the offer
        and get back to you later. Record the preferred date/time and any relevant notes.
        
        Args:
            follow_up_date: The date/time for the follow-up (e.g., "tomorrow morning", "next week")
            follow_up_notes: Any additional notes about what to discuss in the follow-up (optional)
        """
        logger.info(f"[FUNCTION_CALL] schedule_follow_up(follow_up_date='{follow_up_date}', follow_up_notes='{follow_up_notes}')")
        
        session = context.userdata

        session.call_status = "callback_scheduled"

        logger.info(f"Follow-up scheduled → {follow_up_date}")


    @function_tool()
    async def update_call_status(
        self,
        context: RunContext[SalesCallSession],
        status: str
    ):
        """
        Updates the overall status of the sales call.
        
        Use this to mark important milestones in the call progression.
        
        Args:
            status: The call status - "contacted", "interested", "not_interested", 
                   "callback_scheduled", "enrolled", "closed_won", "closed_lost"
        """
        logger.info(f"[FUNCTION_CALL] update_call_status(status='{status}')")
        
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
        logger.info(f"[FUNCTION_CALL] update_whatsapp_number(phone_number='{phone_number}')")
        
        session = context.userdata
        digits = "".join(c for c in str(phone_number).strip() if c.isdigit())
        if len(digits) != 10:
            logger.warning(f"WhatsApp validation failed: expected 10 digits, got {len(digits)}")
            return f"INVALID: WhatsApp number exactly 10 digits होना चाहिए। आपने {len(digits)} digits दिए। कृपया सही 10-digit number बताइए।"
        first = digits[0]
        if first not in "6789":
            logger.warning(f"WhatsApp validation failed: number starts with {first}, must start with 6/7/8/9")
            return "INVALID: India में WhatsApp number 6, 7, 8 या 9 से start होना चाहिए। कृपया सही number बताइए।"
        session.phone_number = digits
        logger.info(f"WhatsApp number validated and stored: {digits}")
        return "valid"

    @function_tool()
    async def cut_call(self, context: RunContext[SalesCallSession]):
        """End the call when the user says goodbye, bye, call खत्म करो, मुझे जाना है, disconnect, etc. Call this to hang up. Say a brief goodbye first, then disconnect after it plays."""
        logger.info("[FUNCTION_CALL] cut_call()")
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
    except Exception as e:
        logger.warning("graceful hangup: session.shutdown error: %s", e)

    async def _delete() -> None:
        try:
            if hasattr(ctx, "delete_room"):
                await ctx.delete_room()
            else:
                await ctx.api.room.delete_room(
                    api.DeleteRoomRequest(room=ctx.room.name)
                )
        except Exception as e:
            logger.debug("graceful hangup: room delete skipped: %s", e)

    try:
        ctx.add_shutdown_callback(_delete)
    except Exception as e:
        logger.debug("graceful hangup: add_shutdown_callback error: %s", e)

    try:
        ctx.shutdown(reason="user no response")
    except Exception as e:
        logger.warning("graceful hangup: ctx.shutdown error: %s", e)


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
        min_speech_duration=0.2,  # lower = catches "हाँ", "yes", "फ़ैशन" etc. (was 0.35 - too high, missed short replies)
        min_silence_duration=0.9,  # longer = less cutting user off mid-sentence, more time to finish thought
        activation_threshold=0.65,  # higher = fewer false "user speaking" when agent speaks (reduces mid-speech stops)
        prefix_padding_duration=0.3,  # capture more context at start of user speech
    )

    
    # This reduces random voice breaking from false VAD triggers
    # Using Gemini 3.1 Flash Live - note: proactive audio and affective dialogue not supported
    llm = google.realtime.RealtimeModel(
        model="gemini-3.1-flash-live-preview",
        language="hi-IN",
        voice="Aoede",
    )

    session = AgentSession[SalesCallSession](
        userdata=session_data,
        llm=llm,
        turn_detection=MultilingualModel(),
        # video_sampler=VoiceActivityVideoSampler(
        #     speaking_fps=0.3,
        #     silent_fps=0.3
        # ),
        user_away_timeout=30,  # seconds before "away" - gives user more time, reduces premature "क्या आप सुन रहे हैं?"
        aec_warmup_duration=2,   # longer AEC warmup = better echo cancellation when agent speaks, fewer mid-speech stops
        allow_interruptions=True,
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
            await asyncio.sleep(15)
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
            # Removed noise_cancellation - using custom RNNoise filter instead
        ),
    )

    # Apply custom RNNoise + Audio Gate filter (config-based)
    try:
        original_audio_input = session.input.audio
        session.input.audio = FilteredAudioInput(source=original_audio_input)
        logger.info("RNNoise + Audio Gate filter applied successfully (config-based)")
    except Exception as e:
        logger.error(f"Failed to apply audio filter: {e}", exc_info=True)


# -------------------------
# Run
# -------------------------

if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint,
        agent_name=os.getenv("AGENT_NAME", "Seema")
        )
    )
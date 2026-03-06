import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from typing import Any, Optional
from dataclasses import dataclass, field

from google.genai import types

import chromadb
from chromadb.config import Settings

from livekit import agents
from livekit.agents import (
    AgentSession,
    Agent,
    RoomInputOptions,
    RunContext,
    function_tool,
)
from livekit.agents.voice.agent_session import VoiceActivityVideoSampler
from livekit.plugins import (
    google,
    noise_cancellation,
    silero,
)

from prompt import AGENT_INSTRUCTIONS

load_dotenv(".env")
from pathlib import Path
import os

load_dotenv( ".env", override=True)
print("CWD:", Path.cwd())
print("LIVEKIT_URL:", os.getenv("LIVEKIT_URL"))
print("GOOGLE_API_KEY:", os.getenv("GOOGLE_API_KEY"))

# Configure logging
log_dir = Path(__file__).parent
log_file = log_dir / "application.log"

# Create logger
logger = logging.getLogger("Hunar-sales-agent")
logger.setLevel(logging.DEBUG)

# Remove existing handlers to avoid duplicates
logger.handlers.clear()

# Create a custom handler that flushes immediately after each log entry
class ImmediateFlushFileHandler(logging.FileHandler):
    """File handler that flushes immediately after each log entry."""
    def emit(self, record):
        super().emit(record)
        self.flush()

# File handler with immediate flushing
file_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler = ImmediateFlushFileHandler(log_file, encoding='utf-8', mode='a', delay=False)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
console_handler.setFormatter(console_formatter)

# Add handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

logger.info(f"Logging initialized. Log file: {log_file}")


def sanitize_text(text: str) -> str:
    """Remove control characters and encoding issues from text."""
    if not text:
        return text
    
    # Remove control characters (like <ctrl46>)
    text = re.sub(r'<ctrl\d+>', '', text)
    
    # Remove other control characters (non-printable except newlines, tabs, etc.)
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]', '', text)
    
    # Clean up extra whitespace
    text = ' '.join(text.split())
    
    return text.strip()


# Initialize ChromaDB for persistent storage
chroma_client = chromadb.PersistentClient(
    path=str(Path(__file__).parent / "chroma_db"),
    settings=Settings(anonymized_telemetry=False)
)

# Get or create collection for sales calls
sales_calls_collection = chroma_client.get_or_create_collection(
    name="sales_calls",
    metadata={"description": "Stores sales call data and lead information"}
)


class SalesCallStorage:
    """Manages persistent storage of SalesCallSession using ChromaDB"""
    
    def __init__(self, collection):
        self.collection = collection
    
    def get_lead_id(self, ctx: agents.JobContext) -> str:
        """Get lead ID - using phone number or participant identity"""
        try:
            # TODO: Extract phone number from context if available
            # For now, use room name or participant identity
            if ctx.room and ctx.room.name:
                return ctx.room.name
            return f"lead_{ctx.job.id if hasattr(ctx.job, 'id') else 'unknown'}"
        except Exception as e:
            logger.warning(f"Could not get lead ID: {e}, using fallback")
            return f"lead_{ctx.job.id if hasattr(ctx.job, 'id') else 'unknown'}"
    
    def load_session(self, lead_id: str) -> Optional['SalesCallSession']:
        """Load lead's SalesCallSession from ChromaDB"""
        try:
            results = self.collection.get(
                ids=[lead_id],
                include=["documents", "metadatas"]
            )
            
            if results["ids"] and len(results["ids"]) > 0:
                document = json.loads(results["documents"][0])
                
                logger.info(f"📞 Loaded call session for lead {lead_id}: Status - {document.get('call_status', 'N/A')}")
                
                conversation_history = document.get("conversation_history", [])
                if len(conversation_history) > 100:
                    conversation_history = conversation_history[-100:]
                
                logger.info(f"📝 Loaded {len(conversation_history)} messages from conversation history")
                
                return SalesCallSession(
                    lead_id=lead_id,
                    lead_name=document.get("lead_name", ""),
                    phone_number=document.get("phone_number", ""),
                    call_status=document.get("call_status", "new"),
                    interest_level=document.get("interest_level", "unknown"),
                    objections_raised=document.get("objections_raised", []),
                    products_discussed=document.get("products_discussed", []),
                    next_follow_up=document.get("next_follow_up", ""),
                    call_duration=document.get("call_duration", 0),
                    call_count=document.get("call_count", 0),
                    conversation_history=conversation_history
                )
            else:
                logger.info(f"🆕 New lead detected: {lead_id}")
                return None
        except Exception as e:
            logger.error(f"Error loading session: {e}", exc_info=True)
            return None
    
    def save_session(self, lead_id: str, session: 'SalesCallSession'):
        """Save SalesCallSession to ChromaDB"""
        try:
            conversation_history = session.conversation_history[-100:] if len(session.conversation_history) > 100 else session.conversation_history
            
            document = {
                "lead_id": lead_id,
                "lead_name": session.lead_name,
                "phone_number": session.phone_number,
                "call_status": session.call_status,
                "interest_level": session.interest_level,
                "objections_raised": session.objections_raised,
                "products_discussed": session.products_discussed,
                "next_follow_up": session.next_follow_up,
                "call_duration": session.call_duration,
                "call_count": session.call_count,
                "conversation_history": conversation_history,
                "last_updated": datetime.now().isoformat()
            }
            
            metadata = {
                "lead_id": lead_id,
                "call_status": session.call_status,
                "interest_level": session.interest_level,
                "call_count": session.call_count,
                "updated_at": datetime.now().timestamp()
            }
            
            self.collection.upsert(
                ids=[lead_id],
                documents=[json.dumps(document)],
                metadatas=[metadata]
            )
            
            logger.info(f"💾 Saved call session for lead {lead_id}: Status - {session.call_status} | Interest - {session.interest_level} | Products - {len(session.products_discussed)}")
        except Exception as e:
            logger.error(f"Error saving session: {e}", exc_info=True)


# Initialize storage manager
call_storage = SalesCallStorage(sales_calls_collection)


@dataclass
class SalesCallSession:
    """Track the sales call session state - Source of Truth"""
    lead_id: str = ""
    lead_name: str = ""
    phone_number: str = ""
    call_status: str = "new"  # new, contacted, interested, not_interested, callback_scheduled, converted
    interest_level: str = "unknown"  # high, medium, low, none
    objections_raised: list[str] = field(default_factory=list)
    products_discussed: list[str] = field(default_factory=list)
    next_follow_up: str = ""
    call_duration: int = 0  # in seconds
    call_count: int = 0
    conversation_history: list[dict[str, Any]] = field(default_factory=list)

def build_agent_context(session: SalesCallSession) -> dict:
    """
    Inject CRM variables into the prompt.
    These replace the variables used in AGENT_INSTRUCTIONS.
    """

    return {
        "Client_Name": session.lead_name or "आप",
        "Client_Gender": "female",  # default if CRM missing
        "Course_Price_Full": os.getenv("COURSE_PRICE_FULL", ""),
        "Course_Price_Discounted": os.getenv("COURSE_PRICE_DISCOUNTED", ""),
        "Installment_1": os.getenv("INSTALLMENT_1", ""),
        "Installment_2": os.getenv("INSTALLMENT_2", ""),
        "Installment_3": os.getenv("INSTALLMENT_3", ""),
        "WhatsApp_Number_Spoken": session.phone_number or ""
    }


class HunarSalesAgent(Agent):
    def __init__(self, session_data: SalesCallSession) -> None:
        init_start = time.time()
        logger.info("Initializing HunarSalesAgent...")

        self.session_data = session_data

        # Build prompt context
        context_vars = build_agent_context(session_data)

        try:
            instructions = AGENT_INSTRUCTIONS

            for key, value in context_vars.items():
                instructions = instructions.replace(f"{{{key}}}", str(value))
        except Exception as e:
            logger.warning(f"Prompt formatting failed: {e}")
            instructions = AGENT_INSTRUCTIONS
        logger.info(f"Final Client Name in Prompt: {context_vars.get('Client_Name')}")
        super().__init__(instructions=instructions)

        init_time = time.time() - init_start
        logger.info(f"HunarSalesAgent initialized in {init_time:.3f}s")

    @function_tool()
    async def start_sales_call(
        self,
        context: RunContext[SalesCallSession],
        lead_name: str = "",
        phone_number: str = "",
    ) -> None:
        """MANDATORY: Call this function silently when you begin a sales call with a prospect.
        
        SILENT EXECUTION: This function executes silently in the background for CRM tracking.
        Do NOT pause, wait, or interrupt your speech when calling this function.
        
        Args:
            lead_name: The prospect's name (if available)
            phone_number: The prospect's phone number (if available)
        """
        start_time = time.time()
        logger.info(f"start_sales_call called - name: {lead_name}, phone: {phone_number}")
        
        session_data = context.userdata
        
        if lead_name:
            session_data.lead_name = lead_name.strip()
        if phone_number:
            session_data.phone_number = phone_number.strip()
        
        session_data.call_count += 1
        session_data.call_status = "contacted"
        
        elapsed = time.time() - start_time
        logger.info(f"📞 SALES CALL STARTED! Lead: {lead_name or 'Unknown'}, Call #: {session_data.call_count} (took {elapsed:.3f}s)")
        return

    @function_tool()
    async def track_interest(
        self,
        context: RunContext[SalesCallSession],
        interest_level: str,
    ) -> None:
        """MANDATORY: Call this function silently to track the prospect's interest level.
        
        SILENT EXECUTION: This function executes silently in the background for CRM tracking.
        
        Args:
            interest_level: The prospect's interest level (high, medium, low, none)
        """
        start_time = time.time()
        logger.debug(f"track_interest called - level: {interest_level}")
        
        session_data = context.userdata
        
        valid_levels = ["high", "medium", "low", "none", "unknown"]
        if interest_level.lower() not in valid_levels:
            logger.warning(f"Invalid interest level '{interest_level}', defaulting to 'unknown'")
            interest_level = "unknown"
        
        old_level = session_data.interest_level
        session_data.interest_level = interest_level.lower()
        
        elapsed = time.time() - start_time
        logger.info(f"📊 INTEREST TRACKED! {old_level} → {interest_level} (took {elapsed:.3f}s)")
        return

    @function_tool()
    async def log_objection(
        self,
        context: RunContext[SalesCallSession],
        objection_type: str,
        objection_details: str = "",
    ) -> None:
        """MANDATORY: Call this function silently when the prospect raises a concern or objection.
        
        SILENT EXECUTION: This function executes silently in the background for CRM tracking.
        
        Args:
            objection_type: Type of objection (price, time, need, competition, other)
            objection_details: Additional details about the objection
        """
        start_time = time.time()
        logger.debug(f"log_objection called - type: {objection_type}")
        
        session_data = context.userdata
        
        objection_entry = {
            "type": objection_type,
            "details": objection_details,
            "timestamp": datetime.now().isoformat()
        }
        session_data.objections_raised.append(objection_entry)
        
        elapsed = time.time() - start_time
        logger.info(f"⚠️ OBJECTION LOGGED! Type: {objection_type} (took {elapsed:.3f}s)")
        return

    @function_tool()
    async def discuss_product(
        self,
        context: RunContext[SalesCallSession],
        product_name: str,
        category: str = "general",
    ) -> None:
        """MANDATORY: Call this function silently when you mention or explain a specific course or product feature.
        
        SILENT EXECUTION: This function executes silently in the background for CRM tracking.
        
        Args:
            product_name: Name of the course or product feature discussed
            category: Category of the product (technology, business, design, etc.)
        """
        start_time = time.time()
        logger.debug(f"discuss_product called - product: {product_name}, category: {category}")
        
        session_data = context.userdata
        
        product_entry = {
            "name": product_name,
            "category": category,
            "timestamp": datetime.now().isoformat()
        }
        session_data.products_discussed.append(product_entry)
        
        elapsed = time.time() - start_time
        logger.info(f"📚 PRODUCT DISCUSSED! {product_name} ({category}) (took {elapsed:.3f}s)")
        return

    @function_tool()
    async def schedule_follow_up(
        self,
        context: RunContext[SalesCallSession],
        follow_up_date: str,
        follow_up_notes: str = "",
    ) -> None:
        """MANDATORY: Call this function silently when you agree on a callback or follow-up action.
        
        SILENT EXECUTION: This function executes silently in the background for CRM tracking.
        
        Args:
            follow_up_date: When to follow up (date/time description)
            follow_up_notes: Notes about what to discuss in follow-up
        """
        start_time = time.time()
        logger.debug(f"schedule_follow_up called - date: {follow_up_date}")
        
        session_data = context.userdata
        
        session_data.next_follow_up = follow_up_date
        session_data.call_status = "callback_scheduled"
        
        elapsed = time.time() - start_time
        logger.info(f"📅 FOLLOW-UP SCHEDULED! Date: {follow_up_date} (took {elapsed:.3f}s)")
        return

    @function_tool()
    async def update_call_status(
        self,
        context: RunContext[SalesCallSession],
        status: str,
    ) -> None:
        """MANDATORY: Call this function silently when the call reaches a conclusion.
        
        SILENT EXECUTION: This function executes silently in the background for CRM tracking.
        
        Args:
            status: Final call status (interested, not_interested, callback_scheduled, converted, contacted)
        """
        start_time = time.time()
        logger.debug(f"update_call_status called - status: {status}")
        
        session_data = context.userdata
        
        valid_statuses = ["new", "contacted", "interested", "not_interested", "callback_scheduled", "converted"]
        if status.lower() not in valid_statuses:
            logger.warning(f"Invalid status '{status}', keeping current status")
            return
        
        old_status = session_data.call_status
        session_data.call_status = status.lower()
        
        elapsed = time.time() - start_time
        logger.info(f"✅ CALL STATUS UPDATED! {old_status} → {status} (took {elapsed:.3f}s)")
        return

async def on_enter(self):
    """Initialize conversation and start script-driven flow"""

    try:
        start_time = time.time()
        logger.info("Agent on_enter triggered")

        session_data = self.session.userdata

        conversation_summary = ""
        objections_summary = ""

        # Build previous conversation context (for follow-ups)
        if session_data and session_data.conversation_history:
            recent_messages = session_data.conversation_history[-15:]

            conversation_lines = []
            for msg in recent_messages:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")

                if content:
                    preview = content[:200] + "..." if len(content) > 200 else content
                    conversation_lines.append(f"{role}: {preview}")

            if conversation_lines:
                conversation_summary = "\n".join(conversation_lines)

        # Build objection summary
        if session_data and session_data.objections_raised:
            recent_objections = session_data.objections_raised[-3:]
            objections_list = [obj.get("type", "unknown") for obj in recent_objections]
            objections_summary = ", ".join(objections_list)

        # Detect follow-up vs new call
        if session_data and session_data.call_count > 0:

            logger.info(
                f"📞 Follow-up call detected for {session_data.lead_name or 'Unknown'}"
            )

            instruction = f"""
This is a FOLLOW-UP sales call.

Lead Name: {session_data.lead_name or "Unknown"}
Previous Call Status: {session_data.call_status}
Interest Level: {session_data.interest_level}

Recent Objections: {objections_summary if objections_summary else "None"}

Previous Conversation:
{conversation_summary if conversation_summary else "No previous conversation available."}

INSTRUCTIONS:
Continue the conversation naturally but follow the scripted conversation flow defined in the system prompt.

If this is a callback that was scheduled earlier, acknowledge that you are calling back.

Start from the most appropriate step of the script based on the previous conversation.
"""

        else:

            logger.info("📞 New outbound call detected")

            instruction = """
Start the conversation strictly from STEP 1 of the script defined in the system prompt.

Follow the exact scripted flow:
STEP 1 → STEP 2 → STEP 3 ... and so on.

Do not skip steps.
Do not summarize steps.
Speak exactly as written in the script.

Begin with STEP 1 — Greeting and Identity Confirmation.
"""

        await self.session.generate_reply(instructions=instruction)

        elapsed = time.time() - start_time
        logger.info(f"Initial conversation started in {elapsed:.3f}s")

    except Exception as e:
        logger.error(f"Error in on_enter: {e}", exc_info=True)

        try:
            await self.session.generate_reply(
                instructions="नमस्ते! क्या मैं आपसे बात कर रही हूँ?"
            )
        except Exception as fallback_error:
            logger.error(
                f"Fallback greeting also failed: {fallback_error}", exc_info=True
            )

    async def on_exit(self):
        """Save SalesCallSession before exiting"""
        start_time = time.time()
        logger.info("Agent on_exit called - saving call session")
        
        try:
            session_data = self.session.userdata
            if session_data and session_data.lead_id:
                call_storage.save_session(session_data.lead_id, session_data)
                logger.info(f"✅ Call session saved for lead {session_data.lead_id}")
                
                try:
                    exit_instruction = "CRITICAL: Say a brief, professional goodbye. Thank them for their time. Keep it under 15 words. DO NOT call any function tools."
                    await self.session.generate_reply(instructions=exit_instruction)
                    logger.info("Exit message generated successfully")
                except Exception as reply_error:
                    logger.debug(f"Could not generate exit message (session may be closing): {reply_error}")
            else:
                logger.info("No session data to save")
                try:
                    exit_instruction = "CRITICAL: Say a brief goodbye. Keep it under 10 words. DO NOT call any function tools."
                    await self.session.generate_reply(instructions=exit_instruction)
                except Exception as reply_error:
                    logger.debug(f"Could not generate exit message: {reply_error}")
            
            elapsed = time.time() - start_time
            logger.info(f"Exit completed in {elapsed:.3f}s")
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Error in on_exit after {elapsed:.3f}s: {e}", exc_info=True)


async def entrypoint(ctx: agents.JobContext):
    entrypoint_start = time.time()
    logger.info("=" * 60)
    logger.info("ENTRYPOINT: Starting Hunar sales agent")
    logger.info(f"Room: {ctx.room.name if ctx.room else 'N/A'}")
    logger.info(f"Job ID: {ctx.job.id if hasattr(ctx.job, 'id') else 'N/A'}")
    
    try:
        # Get lead ID and load SalesCallSession
        lead_id = call_storage.get_lead_id(ctx)
        logger.info(f"Lead ID: {lead_id}")
        
        # Load existing session or create new one
        # sales_session = call_storage.load_session(lead_id)
        # logger.info(f"Lead Name Passed To Agent: {sales_session.lead_name}")
        # if sales_session is None:
        #     sales_session = SalesCallSession(lead_id=lead_id,
        #                                      lead_name="bhuwan",
        #                                      phone_number="9247582746")
        #     logger.info("Created new SalesCallSession")
        # else:
        #     sales_session.lead_id = lead_id
        #     logger.info(f"Loaded existing SalesCallSession: {sales_session.lead_name or 'Unknown'} - {sales_session.call_status}")
        # Always start with a fresh session
        sales_session = SalesCallSession(lead_id=lead_id)

        logger.info("🆕 Starting NEW sales call session (ignoring previous data)")
        session_start = time.time()
        logger.info("Creating AgentSession with Google RealtimeModel...")
        
        # Configure Silero VAD for sales calls
        vad_config = {
            "min_speech_duration": 0.2,
            "min_silence_duration": 0.5,  # Shorter for sales calls
            "activation_threshold": 0.6,
            "prefix_padding_duration": 0.3,
        }
        logger.info(f"Loading Silero VAD with config: {vad_config}")
        vad = silero.VAD.load(**vad_config)
        
        # Configure Google RealtimeModel
        logger.info("Configuring Google RealtimeModel for sales calls")
        llm_model = google.realtime.RealtimeModel(
            model="gemini-live-2.5-flash",
            language="hi-IN",
            voice="Aoede",
            vertexai=True,
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
            proactivity=False,
            enable_affective_dialog=False,
            
        )
        
        # Create session with SalesCallSession as userdata
        session = AgentSession[SalesCallSession](
            userdata=sales_session,
            video_sampler=VoiceActivityVideoSampler(speaking_fps=0.3, silent_fps=0.2),
            user_away_timeout=20,
            llm=llm_model,
            vad=vad,

        )
        session_init_time = time.time() - session_start
        logger.info(f"AgentSession created in {session_init_time:.3f}s")
        
        agent_start = time.time()
        logger.info("Creating HunarSalesAgent instance...")
        agent = HunarSalesAgent(sales_session)
        agent_init_time = time.time() - agent_start
        logger.info(f"HunarSalesAgent created in {agent_init_time:.3f}s")
        
        # Conversation logging handlers
        @session.on("conversation_item_added")
        def on_conversation_item(event):
            """Log conversation items and store in conversation_history"""
            try:
                if hasattr(event, 'item'):
                    item = event.item
                    role = getattr(item, 'role', 'unknown')
                    content = getattr(item, 'content', '')
                    if content:
                        if isinstance(content, str):
                            content = sanitize_text(content)
                        elif isinstance(content, list):
                            content = [sanitize_text(str(c)) if isinstance(c, str) else str(c) for c in content]
                            content = " ".join(str(c) for c in content)
                        else:
                            content = sanitize_text(str(content))
                        
                        if content:
                            logger.info(f"💬 CONVERSATION [{role.upper()}]: {content}")
                            
                            try:
                                session_data = session.userdata
                                if session_data and role in ["user", "assistant"]:
                                    is_duplicate = False
                                    if session_data.conversation_history:
                                        last_message = session_data.conversation_history[-1]
                                        if (last_message.get("role") == role and 
                                            last_message.get("content") == content):
                                            try:
                                                last_timestamp = last_message.get("timestamp", "")
                                                if last_timestamp:
                                                    time_diff = (datetime.now() - datetime.fromisoformat(last_timestamp)).total_seconds()
                                                    if time_diff < 1.0:
                                                        is_duplicate = True
                                                        logger.debug(f"Skipping duplicate message: {content[:50]}...")
                                                else:
                                                    is_duplicate = True
                                            except (ValueError, TypeError):
                                                is_duplicate = True
                                    
                                    if not is_duplicate:
                                        message_entry = {
                                            "role": role,
                                            "content": content,
                                            "timestamp": datetime.now().isoformat()
                                        }
                                        session_data.conversation_history.append(message_entry)
                                        
                                        if len(session_data.conversation_history) > 100:
                                            session_data.conversation_history = session_data.conversation_history[-100:]
                            except Exception as store_error:
                                logger.debug(f"Error storing conversation item: {store_error}")
            except Exception as e:
                logger.debug(f"Error logging conversation item: {e}")
        
        start_time = time.time()
        logger.info("Starting session with conversation logging...")
        
        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
        
        total_time = time.time() - entrypoint_start
        start_elapsed = time.time() - start_time
        logger.info(f"Session started successfully in {start_elapsed:.3f}s")
        logger.info(f"Total entrypoint time: {total_time:.3f}s")
        logger.info("=" * 60)
        
    except Exception as e:
        elapsed = time.time() - entrypoint_start
        logger.error(f"Error in entrypoint after {elapsed:.3f}s: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))

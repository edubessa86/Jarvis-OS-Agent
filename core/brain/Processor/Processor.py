import os
import re
import json
import time
from typing import Dict, Optional, Tuple
import requests
from dotenv import load_dotenv

from core.logger.logger import logger
from core.utils.utils import resolve_pronouns
from core.brain.Processor.FastBrain import fetch_from_groq, make_result
from core.brain.Processor.AgenticBrain import run_agentic_loop
from core.brain.config import (
    ROUTER_API_KEY,
    ROUTER_MODEL,
    ROUTER_ENDPOINT,
    FAST_BRAIN_API_KEY,
    FAST_BRAIN_MODEL,
    FAST_BRAIN_ENDPOINT,
)

load_dotenv()

VALID_DOMAINS = {"coding", "scheduling", "communication", "ui_design", "lifestyle", "general_none"}


class GenericSemanticRouter:
    def __init__(self):
        self.api_key = ROUTER_API_KEY
        self.base_url = ROUTER_ENDPOINT
        self.model = ROUTER_MODEL
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            })
        self.timeout = 2.5

    def analyze_route_full(self, command: str, history_context: str = "") -> Optional[Dict[str, str]]:
        if not self.api_key:
            return None

        trimmed_history = history_context[-350:].strip() if history_context else "No recent history."

        system_prompt = (
            "You are an enterprise AI semantic router. Classify the CURRENT user command into strict JSON "
            "with three keys:\n"
            "1. 'route': Either 'FAST' or 'AGENTIC'.\n"
            "2. 'english_query': A concise 4-10 word pure English translation of the user's core intent (strip Hinglish/fillers).\n"
            "3. 'domain': Choose strictly ONE personalization domain from: "
            "['coding', 'scheduling', 'communication', 'ui_design', 'lifestyle', 'general_none'].\n"
            "   - Use 'scheduling' ONLY for study plans, timetables, routines, or productivity schedules.\n"
            "   - Use 'coding' ONLY for writing code, building apps/scripts, or debugging.\n"
            "   - Use 'ui_design' ONLY for designing web pages, dashboards, CSS, or frontend layouts.\n"
            "   - Use 'communication' ONLY for drafting/sending emails, WhatsApp, or Telegram messages.\n"
            "   - Use 'lifestyle' ONLY for workout, diet, health, or daily habit advice.\n"
            "   - Use 'general_none' for EVERYTHING ELSE (factual lookups, weather, opening apps, hardware control, reading files, memory recall, terminal commands).\n\n"
            "CRITICAL RULE: Evaluate ONLY the [USER COMMAND] for routing. Use [RECENT CONVERSATION HISTORY] purely for context. "
            "If the new command contains MULTIPLE intents, ALWAYS prioritize routing to 'AGENTIC'.\n\n"
            "### FAST BRAIN RULES (ROUTE TO 'FAST' ONLY IF STRICTLY THESE)\n"
            "- Casual conversation, greetings, jokes, time, date, or personal chit-chat.\n"
            "- Simple hardware controls: volume up/down/mute, brightness, screen lock, sleep, or screenshots.\n"
            "- Simple app/web launching or closing: 'Open Chrome', 'Close Notepad', 'Launch YouTube'.\n"
            "- Direct media playback: 'Play [song/video] on YouTube'.\n"
            "- Simple real-time web lookups: weather forecasts, live scores, quick definitions, or news.\n"
            "- Simple factual 'How-to' queries: 'How to boil an egg', 'How to tie a tie'.\n\n"
            "### AGENTIC BRAIN RULES (ROUTE TO 'AGENTIC' IF REQUIRED)\n"
            "- Technical/Complex 'How-to' queries: 'How does my database work', 'How to code this script'.\n"
            "- Mobile Device Control: 'turn on phone hotspot', 'read my mobile SMS'.\n"
            "- Email or WhatsApp messaging.\n"
            "- File system CRUD operations.\n"
            "- Coding & Terminal: writing/executing scripts, CMD commands, pip/npm, or git.\n"
            "- Long-term memory retrieval: searching notes, facts, or calendar.\n"
            "- Advanced research: scraping, arxiv search, or deep research.\n"
            "- Compound multi-step workflows.\n"
            "- Image generation, image editing, logo/wallpaper/poster creation, or Image/Screen analysis.\n"
            "- ANY command modifying or analyzing a local file/system state.\n\n"
            "### FEW-SHOT EXAMPLES\n"
            "User: 'Hello Jarvis' -> {\"route\": \"FAST\", \"english_query\": \"greeting hello\", \"domain\": \"general_none\"}\n"
            "User: 'Hi Jarvis, volume badha do' -> {\"route\": \"FAST\", \"english_query\": \"increase system volume\", \"domain\": \"general_none\"}\n"
            "User: 'Mera 7 din ka study plan banao' -> {\"route\": \"AGENTIC\", \"english_query\": \"create a 7 day study schedule plan\", \"domain\": \"scheduling\"}\n"
            "User: 'Jarvis, Kaif ko email bhej do ki main late aaunga' -> {\"route\": \"AGENTIC\", \"english_query\": \"send email to Kaif about being late\", \"domain\": \"communication\"}\n"
            "User: 'Mere liye ek portfolio dashboard banao' -> {\"route\": \"AGENTIC\", \"english_query\": \"build a portfolio web dashboard\", \"domain\": \"ui_design\"}\n"
            "User: 'Mere father ka naam kya hai?' -> {\"route\": \"AGENTIC\", \"english_query\": \"what is my father name\", \"domain\": \"general_none\"}"
        )

        user_content = f"[RECENT CONVERSATION HISTORY]\n{trimmed_history}\n\n[USER COMMAND]\n\"{command}\""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.0,
            "max_tokens": 60,
            "response_format": {"type": "json_object"}
        }

        try:
            start_ts = time.perf_counter()
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=self.timeout
            )
            latency_ms = (time.perf_counter() - start_ts) * 1000

            if response.status_code == 200:
                raw_json = response.json()["choices"][0]["message"]["content"]
                data = json.loads(raw_json)
                route = "AGENTIC" if str(data.get("route", "FAST")).strip().upper() == "AGENTIC" else "FAST"
                eng_q = str(data.get("english_query") or command).strip()
                domain = str(data.get("domain") or "general_none").strip().lower()
                if domain not in VALID_DOMAINS:
                    domain = "general_none"
                logger.info(
                    f"Semantic Router [{latency_ms:.1f}ms] | Decision -> {route} | Domain -> {domain} | Eng -> '{eng_q[:45]}'"
                )
                return {"route": route, "english_query": eng_q, "domain": domain}
            else:
                logger.warning(f"Router API non-200 status ({response.status_code}): {response.text[:100]}")

        except Exception as e:
            logger.warning(f"Router API failed ({e}). Attempting Fast Brain (Tier 2) fallback...")
            try:
                if FAST_BRAIN_API_KEY:
                    fallback_session = requests.Session()
                    fallback_session.headers.update({
                        "Authorization": f"Bearer {FAST_BRAIN_API_KEY}",
                        "Content-Type": "application/json"
                    })

                    fallback_payload = {
                        "model": FAST_BRAIN_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.0,
                        "max_tokens": 60,
                        "response_format": {"type": "json_object"}
                    }

                    start_ts_fb = time.perf_counter()
                    response_fb = fallback_session.post(
                        f"{FAST_BRAIN_ENDPOINT}/chat/completions",
                        json=fallback_payload,
                        timeout=self.timeout
                    )
                    latency_ms_fb = (time.perf_counter() - start_ts_fb) * 1000

                    if response_fb.status_code == 200:
                        raw_json_fb = response_fb.json()["choices"][0]["message"]["content"]
                        data_fb = json.loads(raw_json_fb)
                        route_fb = "AGENTIC" if str(data_fb.get("route", "FAST")).strip().upper() == "AGENTIC" else "FAST"
                        eng_q_fb = str(data_fb.get("english_query") or command).strip()
                        domain_fb = str(data_fb.get("domain") or "general_none").strip().lower()
                        if domain_fb not in VALID_DOMAINS:
                            domain_fb = "general_none"
                        logger.info(
                            f"Tier 2 Semantic Router [{latency_ms_fb:.1f}ms] | Decision -> {route_fb} | Domain -> {domain_fb}"
                        )
                        return {"route": route_fb, "english_query": eng_q_fb, "domain": domain_fb}
                    else:
                        logger.warning(f"Tier 2 Router API non-200 status ({response_fb.status_code}): {response_fb.text[:100]}")
            except Exception as e2:
                logger.warning(f"Tier 2 Router API failed ({e2}). Switching to Local Regex Fallback (Tier 3).")

        return None

    def analyze_route(self, command: str, history_context: str = "") -> Optional[str]:
        res = self.analyze_route_full(command, history_context)
        return res["route"] if res else None


router_engine = GenericSemanticRouter()


def infer_local_domain(command: str) -> str:
    cmd_lower = command.lower().strip()
    if re.search(r'\b(schedule|timetable|study plan|routine|plan|time table|slots)\b', cmd_lower):
        return "scheduling"
    if re.search(r'\b(dashboard|ui|frontend|css|html|website|webpage|design|layout|theme)\b', cmd_lower):
        return "ui_design"
    if re.search(r'\b(python|script|code|function|class|bug|debug|react|server|api)\b', cmd_lower):
        return "coding"
    if re.search(r'\b(email|mail|gmail|whatsapp|telegram|message|msg|draft|write to|bhejo)\b', cmd_lower):
        return "communication"
    if re.search(r'\b(workout|diet|exercise|gym|health|sleep routine)\b', cmd_lower):
        return "lifestyle"
    return "general_none"


def get_local_fallback_route(command: str) -> str:
    cmd_lower = command.lower().strip()

    absolute_fast_patterns = [
        r'\b(volume|awaaz|sound|brightness|screenshot|lock|sleep|mute)\b',
        r'\b(open|kholo|close|band|start|launch)\b',
        r'\b(play|chalao|song|gana|music|youtube)\b',
        r'\b(weather|mausam|time|date|score|news|joke)\b',
        r'\b(hi|hello|hey|kaise ho|what is up|good morning|good evening)\b',
        r'^\s*(what is|kya hai|kya hota hai|who is|kaun hai)\s+\w+\s*$'
    ]

    agentic_strict_patterns = [
        r'\b(write|run|execute|create|make|generate|edit|banao|likho|chalao).{0,30}(python|script|code|file|folder|dir|react|app|server|image|photo|picture|pic|wallpaper|logo|poster|art|illustration)\b',
        r'\b(image|photo|picture|pic|wallpaper|logo|poster|art|illustration).{0,30}(generate|create|make|edit|banao|bana do|bnado)\b',
        r'\b(send|write|bhejo|read|check|karo).{0,30}(email|mail|gmail|whatsapp|msg|message|telegram)\b',
        r'\b(analyze|analyse|read|describe|explain|dekho|dikhao).{0,30}(image|photo|picture|screenshot|screen)\b',
        r'\b(search|find|check|yaad|recall|batao|kahan).{0,30}(memory|history|vault|notes|kal|aaj)\b',
        r'\b(terminal|cmd|powershell|pip install|npm install|git clone|subprocess)\b',
        r'\b(arxiv|vault|deep research|scrape|webpage)\b',
        r'\b(calendar|reminder|event|schedule)\b',
        r'\b(turn on|turn off|connect|read|send|check|control|chalu|band|on|off).{0,30}(mobile|phone|smartphone|sms|hotspot|flight mode|wifi)\b',
        r'\b(mobile|phone).{0,30}(control|connect|hotspot|sms)\b',
        r'\b(how to).{0,30}(code|build|fix|error|debug|install)\b',
        r'\b(kaise).{0,30}(code|banayein|fix|error|install)\b'
    ]

    for pattern in agentic_strict_patterns:
        if re.search(pattern, cmd_lower):
            return "AGENTIC"

    for pattern in absolute_fast_patterns:
        if re.search(pattern, cmd_lower):
            return "FAST"

    action_words = re.findall(r'\b(and|aur|then|phir|baad mein)\b', cmd_lower)
    if len(action_words) > 0:
        return "AGENTIC"

    return "FAST"


def get_route_decision(command: str, memory_instance=None) -> str:
    if memory_instance and hasattr(memory_instance, "ephemeral"):
        if memory_instance.ephemeral.get("waiting_for_confirmation"):
            cmd_lower = command.lower().strip()
            confirm_pattern = r'\b(haa|ha|han|yes|yup|yeah|sure|ok|okay|bilkul|zaroor|theek hai|thik hai|do it|kar do|kardo|krdo|kar de|krde|bhej do|update kar do)\b'
            if re.search(confirm_pattern, cmd_lower):
                memory_instance.ephemeral["waiting_for_confirmation"] = False
                memory_instance.ephemeral["proactive_confirmed"] = True
                memory_instance.ephemeral["current_english_query"] = command
                memory_instance.ephemeral["current_domain"] = "general_none"
                logger.info("Proactive Confirmation detected -> Routing directly to AGENTIC")
                return "AGENTIC"
            else:
                if hasattr(memory_instance, "clear_pending_confirmation"):
                    memory_instance.clear_pending_confirmation()
                else:
                    memory_instance.ephemeral["waiting_for_confirmation"] = False
                    memory_instance.ephemeral.pop("pending_task_data", None)

    history_context = ""
    if memory_instance and hasattr(memory_instance, "get_fast_history_context"):
        try:
            history_context = memory_instance.get_fast_history_context()
        except Exception:
            history_context = ""

    cloud_result = router_engine.analyze_route_full(command, history_context)
    if cloud_result:
        if memory_instance and hasattr(memory_instance, "ephemeral"):
            memory_instance.ephemeral["current_english_query"] = cloud_result.get("english_query", command)
            memory_instance.ephemeral["current_domain"] = cloud_result.get("domain", "general_none")
        return cloud_result["route"]

    logger.info("Using Local Rule-Based Fallback Router...")
    fallback_route = get_local_fallback_route(command)
    if memory_instance and hasattr(memory_instance, "ephemeral"):
        memory_instance.ephemeral["current_english_query"] = command
        memory_instance.ephemeral["current_domain"] = infer_local_domain(command)
    return fallback_route


def fetch_hybrid_response(raw_command: str, memory_instance=None) -> Optional[Dict[str, any]]:
    try:
        decision = get_route_decision(raw_command, memory_instance)

        if decision == "AGENTIC":
            logger.info("Smart Router: AGENTIC (Deep Tasks, Memory, Comms & Visual Analysis)")
            context_blocks = []

            is_silent = False
            if memory_instance:
                if hasattr(memory_instance, "consume_silent_flag"):
                    is_silent = memory_instance.consume_silent_flag()
                elif hasattr(memory_instance, "ephemeral"):
                    is_silent = bool(memory_instance.ephemeral.pop("force_silent_agentic", False))

                if hasattr(memory_instance, "get_and_clear_feedback"):
                    memory_instance.get_and_clear_feedback()

                try:
                    logger.info("Fetching Initial Profile, Mood & Workspace Context...")
                    personal_context = memory_instance.get_relevant_context(raw_command)
                    if personal_context:
                        context_blocks.append(personal_context)
                except Exception as e:
                    logger.error(f"Memory Fetch Error: {e}")

                if hasattr(memory_instance, "ephemeral") and memory_instance.ephemeral.get("proactive_confirmed"):
                    pending_data = (
                        memory_instance.consume_pending_confirmation()
                        if hasattr(memory_instance, "consume_pending_confirmation")
                        else memory_instance.ephemeral.pop("pending_task_data", None)
                    )
                    memory_instance.ephemeral.pop("proactive_confirmed", None)
                    if isinstance(pending_data, dict):
                        proactive_block = (
                            "[CONFIRMED PROACTIVE TASK DETAILS]\n"
                            f"Original Proactive Instruction: {pending_data.get('agent_command', '')}\n"
                            f"Event Trigger Data:\n{pending_data.get('batched_data', '')}\n"
                            f"Question Asked to User: {pending_data.get('proactive_question', '')}\n"
                            "User has just confirmed this action. Execute the required tool now."
                        )
                        context_blocks.append(proactive_block)

            final_context = "\n".join(context_blocks)

            return run_agentic_loop(raw_command, final_context, memory_instance, silent=is_silent)
        else:
            logger.info("Smart Router: FAST (Direct Apps / Stateless Chat / Hardware)")

            if memory_instance and hasattr(memory_instance, 'get_and_clear_feedback'):
                cleared_feedback = memory_instance.get_and_clear_feedback()
                if cleared_feedback:
                    logger.info(f"Flushed pending live feedback: {cleared_feedback}")

            ephemeral = memory_instance.ephemeral if memory_instance and hasattr(memory_instance, "ephemeral") else None
            return fetch_from_groq(raw_command, memory_instance, ephemeral)

    except Exception as e:
        logger.error(f"Smart Router Error: {e}. Defaulting to Fast Brain.")

        if memory_instance and hasattr(memory_instance, 'get_and_clear_feedback'):
            memory_instance.get_and_clear_feedback()

        ephemeral = memory_instance.ephemeral if memory_instance and hasattr(memory_instance, "ephemeral") else None
        return fetch_from_groq(raw_command, memory_instance, ephemeral)


def process_command(raw_command: str, memory_instance=None) -> Dict[str, any]:
    resolved_command = resolve_pronouns(raw_command)
    result = fetch_hybrid_response(resolved_command, memory_instance)

    if not result:
        return make_result("Connection failed. Please check your internet connection.", priority="low")

    return result
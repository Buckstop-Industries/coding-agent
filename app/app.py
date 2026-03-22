import os
import subprocess
import logging
import json
import re
import time
import shutil
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from pathlib import Path
from threading import Lock

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", 2000000))
CONTEXT_THRESHOLD = 0.90
TIMEOUT_SECONDS = int(os.environ.get("AGENT_TIMEOUT", 1800))
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
SESSIONS_ROOT = WORKSPACE_ROOT / "sessions"

# Global lock to prevent concurrent operations in the same container
execution_lock = Lock()

# Initialize Bolt app
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

def generate_gemini_settings():
    """
    Dynamically generates the settings.json for Gemini CLI.
    """
    # Load subagents from fleet_config.json
    subagents = []
    config_path = WORKSPACE_ROOT / "fleet_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config_data = json.load(f)
                subagents = config_data.get("subagents", [])
                
                for agent in subagents:
                    if "system_prompt_file" in agent:
                        prompt_path = WORKSPACE_ROOT / agent.pop("system_prompt_file")
                        if prompt_path.exists():
                            with open(prompt_path, "r") as pf:
                                agent["system_prompt"] = pf.read()
                        else:
                            logger.warning(f"Prompt file not found: {prompt_path}")
                            agent["system_prompt"] = f"Warning: Prompt file {prompt_path.name} not found."
                            
                logger.info(f"Loaded {len(subagents)} subagents from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load fleet_config.json: {e}")

    settings = {
        "mcp_servers": {
            "gitlab": {
                "command": "npx",
                "args": ["-y", "@structured-world/gitlab-mcp"],
                "env": {
                    "GITLAB_PAT": os.environ.get("GITLAB_PAT"),
                    "GITLAB_URL": os.environ.get("GITLAB_URL", "https://gitlab.com")
                }
            },
            "sentry": {
                "command": "npx",
                "args": ["-y", "@sentry/mcp-server"],
                "env": {
                    "SENTRY_AUTH_TOKEN": os.environ.get("SENTRY_TOKEN")
                }
            },
            "asana": {
                "command": "npx",
                "args": ["-y", "@roychri/mcp-server-asana"],
                "env": {
                    "ASANA_ACCESS_TOKEN": os.environ.get("ASANA_PAT")
                }
            }
        },
        "extensions": {
            "maestro": {
                "subagents": subagents
            }
        }
    }
    
    settings_dir = Path.home() / ".gemini"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.json"
    
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=4)
    
    logger.info(f"Gemini settings generated at {settings_path}")

def get_dir_size(path):
    """
    Calculates the total size of a directory in bytes.
    """
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    except Exception as e:
        logger.error(f"Error calculating size for {path}: {e}")
    return total

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def handle_admin_commands(command, say, thread_ts):
    """
    Handles fleet management commands like status, cleanup, and context.
    """
    cmd_lower = command.lower()
    
    if cmd_lower == "fleet status" or cmd_lower == "fleet context status":
        if not SESSIONS_ROOT.exists():
            say("📁 **Fleet Status:** No sessions root found.", thread_ts=thread_ts)
            return True
        
        sessions = []
        for session_dir in SESSIONS_ROOT.iterdir():
            if session_dir.is_dir():
                size = get_dir_size(session_dir)
                usage_file = session_dir / ".context_usage"
                usage_info = ""
                if usage_file.exists():
                    try:
                        usage_info = f" | 🧠 {usage_file.read_text().strip()}"
                    except Exception:
                        pass
                
                mtime = time.strftime('%m-%d %H:%M', time.localtime(session_dir.stat().st_mtime))
                sessions.append(f"• `{session_dir.name}`: {format_size(size)}{usage_info} (Active: {mtime})")
        
        if not sessions:
            say("📁 **Fleet Status:** No active sessions found.", thread_ts=thread_ts)
        else:
            report = "\n".join(sessions)
            say(f"📋 **Active Fleet Sessions:**\n{report}", thread_ts=thread_ts)
        return True

    if cmd_lower.startswith("fleet context"):
        parts = command.split()
        if len(parts) == 2 or (len(parts) == 3 and parts[2] == "status"):
            return handle_admin_commands("fleet status", say, thread_ts)
        
        if len(parts) == 4 and parts[2] == "limit":
            try:
                global MAX_TOKENS
                MAX_TOKENS = int(parts[3])
                say(f"⚙️ **Fleet Config:** `MAX_TOKENS` updated to `{MAX_TOKENS:,}`", thread_ts=thread_ts)
            except ValueError:
                say("⚠️ **Error:** Please provide a valid number for the limit.", thread_ts=thread_ts)
            return True
            
        say("💡 Usage:\n• `fleet context status`\n• `fleet context limit <number>`", thread_ts=thread_ts)
        return True

    if cmd_lower.startswith("fleet cleanup"):
        parts = command.split()
        if len(parts) < 3:
            say("💡 Usage: `fleet cleanup <session_id|all>`", thread_ts=thread_ts)
            return True
        
        target = parts[2]
        if target == "all":
            if SESSIONS_ROOT.exists():
                shutil.rmtree(SESSIONS_ROOT)
                SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
                say("🧹 **Fleet Cleanup:** All sessions have been purged.", thread_ts=thread_ts)
            else:
                say("🧹 **Fleet Cleanup:** No sessions to clean.", thread_ts=thread_ts)
            return True
        
        target_dir = SESSIONS_ROOT / target
        if target_dir.exists() and target_dir.is_dir():
            shutil.rmtree(target_dir)
            say(f"🧹 **Fleet Cleanup:** Session `{target}` has been deleted.", thread_ts=thread_ts)
        else:
            say(f"⚠️ **Error:** Session `{target}` not found.", thread_ts=thread_ts)
        return True

    return False

def check_for_errors(line):
    if "TerminalQuotaError" in line or "Quota exceeded" in line:
        retry_match = re.search(r"Please retry in ([\d\.]+)s", line)
        wait_time = f" (Retry in {retry_match.group(1)}s)" if retry_match else ""
        return f"🛑 **QUOTA EXCEEDED:** You have exhausted your Gemini API quota.{wait_time}"
    if "Invalid configuration" in line:
        return "❌ **CONFIG ERROR:** The Gemini CLI settings.json is invalid."
    if "unexpected critical error" in line.lower():
        return "💥 **CRITICAL ERROR:** An unexpected error occurred in the Gemini CLI."
    return None

@app.event("app_mention")
def handle_mention(event, say):
    handle_interaction(event, say)

@app.event("message")
def handle_message_events(event, say):
    if "thread_ts" not in event or event.get("bot_id"):
        return
    handle_interaction(event, say)

def handle_interaction(event, say):
    """
    Common logic for mentions and threaded messages using stream-json.
    """
    thread_ts = event.get("thread_ts", event.get("ts"))
    raw_text = event.get("text", "")
    
    clean_text = re.sub(r"<@U[A-Z0-9]+>", "", raw_text).strip()
    
    if not clean_text:
        if event.get("type") == "app_mention":
            say("I'm listening! Please provide a prompt.", thread_ts=thread_ts)
        return

    if handle_admin_commands(clean_text, say, thread_ts):
        return

    session_dir = SESSIONS_ROOT / thread_ts
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Symlink shared resources to the session directory so the agent can find them locally
    shared_resources = ["GEMINI.md", "fleet_config.json", "prompts", "scripts"]
    for resource in shared_resources:
        source = WORKSPACE_ROOT / resource
        target = session_dir / resource
        if source.exists() and not target.exists():
            try:
                target.symlink_to(source)
            except Exception as e:
                logger.error(f"Failed to symlink {resource}: {e}")
    
    if execution_lock.locked():
        say("⏳ **Fleet Queue Busy:** Your request is queued and will start shortly.", thread_ts=thread_ts)

    with execution_lock:
        say(f"🚀 Fleet Agent initializing sub-tasks for: `{clean_text[:50]}...`", thread_ts=thread_ts)
        
        start_time = time.time()
        try:
            # Check for existing session in this thread directory to enable persistence
            cmd = ["gemini", "--yolo", "--include-directories", str(WORKSPACE_ROOT), "--output-format", "stream-json"]
            
            # If we've already run in this session, resume the latest state
            if (session_dir / ".gemini").exists():
                cmd += ["--resume", "latest"]
                
            cmd.append(clean_text)

            logger.info(f"Executing Gemini command: {' '.join(cmd)} in CWD: {session_dir}")

            # Run gemini with stream-json for real-time structured events
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(session_dir)
            )
            
            full_response = ""
            last_usage = None
            error_detected = None
            last_slack_update = time.time()
            
            if process.stdout:
                for line in process.stdout:
                    line_strip = line.strip()
                    if not line_strip:
                        continue
                    
                    # Check for CLI errors in non-JSON output (e.g. startup errors)
                    if not line_strip.startswith("{"):
                        logger.info(f"CLI Raw: {line_strip}")
                        detected = check_for_errors(line_strip)
                        if detected:
                            error_detected = detected
                        continue

                    try:
                        event_data = json.loads(line_strip)
                        event_type = event_data.get("type")
                        
                        if event_type == "message":
                            # Assistant's response chunks
                            if event_data.get("role") == "assistant":
                                chunk = event_data.get("content", "")
                                full_response += chunk
                                
                                # Periodically flush partial responses to Slack for better interactivity
                                if "\n" in chunk or len(full_response) > 500:
                                    # We don't want to spam 'say' for every character, but seeing the plan form is good
                                    # For now, we'll still send the full response at the end, 
                                    # but this ensures the process log shows it.
                                    last_slack_update = time.time()
                        
                        elif event_type == "tool_use":
                            # Tool call initiation
                            tool_name = event_data.get("tool_name")
                            tool_input = event_data.get("input", {})
                            agent_name = event_data.get("agent_name", "Lead")
                            
                            # Surface subagent delegation and nested tool calls
                            if tool_name == "maestro":
                                subagent_name = tool_input.get("subagent", "unknown")
                                say(f"🤖 **Delegating to Specialist:** `{subagent_name}`...", thread_ts=thread_ts)
                            elif tool_name == "generalist":
                                say(f"🤖 **Delegating to Generalist subagent...**", thread_ts=thread_ts)
                            else:
                                prefix = f"🤖 **[{agent_name}]** " if agent_name != "Lead" else "🛠️ **Acting:** "
                                say(f"{prefix}Executing `{tool_name}`...", thread_ts=thread_ts)
                            
                        elif event_type == "result":
                            # Final token usage metadata and execution status
                            stats = event_data.get("stats", {})
                            last_usage = stats.get("total_tokens")
                            if last_usage:
                                usage_str = f"{last_usage:,} / {MAX_TOKENS:,} tokens"
                                with open(session_dir / ".context_usage", "w") as f:
                                    f.write(usage_str)
                                # Immediate threshold check
                                if last_usage / MAX_TOKENS > CONTEXT_THRESHOLD:
                                    say(f"⚠️ **CRITICAL: Context limit reached ({last_usage:,}/{MAX_TOKENS:,})!** Terminating.", thread_ts=thread_ts)
                                    process.terminate()
                                    break

                    except json.JSONDecodeError:
                        continue

                    if time.time() - start_time > TIMEOUT_SECONDS:
                        say(f"🛑 **TIMEOUT: Subagent exceeded {TIMEOUT_SECONDS}s.** Terminating.", thread_ts=thread_ts)
                        process.terminate()
                        break
                    
            process.wait()
            
            final_usage_msg = ""
            if last_usage:
                final_usage_msg = f"\n\n📊 **Context Usage:** {last_usage:,} / {MAX_TOKENS:,} tokens"
            
            if full_response:
                for i in range(0, len(full_response), 3000):
                    say(f"```\n{full_response[i:i+3000]}\n```", thread_ts=thread_ts)
                
            if error_detected:
                say(error_detected, thread_ts=thread_ts)
            else:
                say(f"✅ Execution Finished.{final_usage_msg}", thread_ts=thread_ts)
                
        except Exception as e:
            logger.error(f"Error executing gemini: {e}")
            say(f"❌ Error: {e}", thread_ts=thread_ts)

if __name__ == "__main__":
    generate_gemini_settings()
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()

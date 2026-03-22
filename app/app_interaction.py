def handle_interaction(event, say):
    """
    Common logic for mentions and threaded messages.
    """
    thread_ts = event.get("thread_ts", event.get("ts"))
    raw_text = event.get("text", "")
    
    # Robustly strip ALL mentions (<@U...>) to isolate the actual command
    # This prevents the bot from treating its own mention as part of the prompt
    clean_text = re.sub(r"<@U[A-Z0-9]+>", "", raw_text).strip()
    
    if not clean_text:
        if event.get("type") == "app_mention":
            say("I'm listening! Please provide a prompt.", thread_ts=thread_ts)
        return

    # Handle Admin Commands using the cleaned text
    if handle_admin_commands(clean_text, say, thread_ts):
        return

    # Thread Isolation
    session_dir = SESSIONS_ROOT / thread_ts
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Concurrency Lock
    if execution_lock.locked():
        say("⏳ **Fleet Queue Busy:** Your request is queued and will start shortly.", thread_ts=thread_ts)

    with execution_lock:
        say(f"🚀 Fleet Agent initializing sub-tasks for: `{clean_text[:50]}...`", thread_ts=thread_ts)
        
        start_time = time.time()
        try:
            # Run gemini --yolo in the session-specific directory
            process = subprocess.Popen(
                ["gemini", "--yolo", clean_text],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(session_dir)
            )
            
            output_buffer = ""
            last_context_usage = (0, 0)
            error_detected = None
            
            for line in process.stdout:
                output_buffer += line
                logger.info(line.strip())
                
                detected = check_for_errors(line)
                if detected: error_detected = detected

                current, limit = parse_context_usage(line)
                if current:
                    last_context_usage = (current, limit)
                    # Persist usage for admin commands
                    with open(session_dir / ".context_usage", "w") as f:
                        f.write(f"{current:,} / {limit:,} tokens")
                    
                    if current / limit > CONTEXT_THRESHOLD:
                        say(f"⚠️ **CRITICAL: Context limit reached ({current}/{limit})!** Terminating.", thread_ts=thread_ts)
                        process.terminate()
                        break

                if time.time() - start_time > TIMEOUT_SECONDS:
                    say(f"🛑 **TIMEOUT: Subagent has been running for {TIMEOUT_SECONDS}s.** Terminating.", thread_ts=thread_ts)
                    process.terminate()
                    break
                    
            process.wait()
            
            usage_msg = f"\n\n📊 **Context Usage:** {last_context_usage[0]:,} / {last_context_usage[1]:,} tokens" if last_context_usage[0] > 0 else ""
            
            for i in range(0, len(output_buffer), 3000):
                say(f"```\n{output_buffer[i:i+3000]}\n```", thread_ts=thread_ts)
                
            if error_detected:
                say(error_detected, thread_ts=thread_ts)
            else:
                say(f"✅ Execution Finished.{usage_msg}", thread_ts=thread_ts)
                
        except Exception as e:
            logger.error(f"Error executing gemini: {e}")
            say(f"❌ Error: {e}", thread_ts=thread_ts)

if __name__ == "__main__":
    generate_gemini_settings()
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()

# LeepaBot

LeepaBot is a highly conversational, context-aware Discord AI companion.

LeepaBot is designed to organically read the room, match the ambient energy, and decide autonomously when to jump into a conversation, drop a reaction, or completely ignore a prompt. She operates on a multi-model architecture, seamlessly routing requests through various LLM providers (Gemini, Groq, DeepSeek, OpenRouter) to maximize API efficiency on free-tier infrastructure.

## Architecture

The backend is written in Python using `discord.py`. It offloads the computational heavy lifting to external APIs, keeping the bot lightweight. The system is deployed on a Google Cloud Platform e2-micro Linux server, utilizing a systemd daemon to achieve permanent 24/7 uptime without relying on local hardware. It can, however, be hosted locally.

A core feature is her memory management, which has been heavily iterated on. Instead of feeding the whole chat history to the LLM and burning tokens, the bot breathes. Every 25 messages, it slices the overflow and compresses it into a dense running summary via a non-blocking background thread.

Crucially, the long-term memory (LTM) RAG pipeline has been completely nuked. Experience showed that LTM often acted as a toxic compounding loop, where the bot would anchor too heavily to past behaviors or misinterpretations. LeepaBot now operates completely statelessly across sessions. She relies purely on immediate contextual awareness and her short-term memory queue. This simpler architecture has resulted in an improved behavior over long term sessions. The deprecated relevant file is lore_vector_store.py.

To handle multi-modal image awareness without destroying the token economy, LeepaBot utilizes a decoupled Visual Pre-Processor. Instead of feeding raw images into the main text LLM, the system intercepts Discord image attachments at the edge. A lightweight vision model (spcifically Gemini Flash or a fallback Llama Vision model via OpenRouter) translates the pixels into a dense plain-text description. This description is then injected directly into the short-term memory queue. To the main reasoning engine, it simply looks like a user described the image in chat, granting full visual context with zero structural bloat.

To prevent chat spam, the bot features an Interception Matrix. If another potential bot answers a message while LeepaBot is still generating a response, the parallel thread flips a kill-switch and aborts text generation. Direct user pings or explicit image uploads grant an immunity flag to bypass this lock.

## Cognitive Pipeline & Persona

To improve LeepaBot's answers, a cognitive mode is simulated with each response. Every response starts with a hidden `thinking_block`. She analyzes intent, reads the social dynamics, anchors her emotional state, and decides on a comedic angle in the same token-call as her answer. This forces the LLM to write for itself what direction it wants to take with the answer, giving itself the opportunity to analyze potential meanings of the incoming message and take different directions with its answer

Her personality has been fined tuned using the Gemini 3 flash preview model. Experience has shown that LLMs love to ask rhetorical questions, as well as repeat keywords from incoming message with a question mark. This has proved to be annoying, so LeepaBot has a strict rule never to ask questions. This inclusion has shown itself to be an improvement.

Her persona, rules, and N-shot examples are decoupled from the routing logic and stored entirely in a separate `prompts.py` file, making behavioral tweaks easy and keeping the `api_handler` completely clean. She also has access to a global, centralized inventory of custom server emojis to deliver punchlines purely through physical reactions when words would ruin the comedic timing. All of the `prompts.py` file is where personalization of the bot happens. Her personality, N-shot examples and custom emojis can be changed to fit any server.

## Setup Instructions

1. Clone the repository to your target machine.
2. Install the required dependencies using `pip install -r requirements.txt` within a virtual environment.
3. Create a `.env` file in the root directory to securely house your `DISCORD_TOKEN`, `BAPT_DISCORD_ID`, `OTHER_BOT_ID`, and the relevant API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, etc.). Note: `BAPT_DISCORD_ID` should be the bot owner's ID.
4. Modify `core/prompts.py` to tweak her baseline persona, N-shots, or add new custom emojis to her arsenal.
5. To achieve permanent uptime on a Linux server, create a systemd service file pointing to your Python executable and the `main.py` script. Enabling and starting this daemon allows the OS to automatically restart the bot in the event of a crash. Future updates are handled by running `git pull`.

## Future Plans

The architecture is functional and fully deployed to the cloud. No new integrations are planned for now. LeepaBot is considered to be complete.
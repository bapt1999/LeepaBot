# LeepaBot

LeepaBot is a highly conversational, context-aware Discord AI companion.

The main idea here is that chatbots usually just wait for strict commands and reply blindly. LeepaBot is designed to organically read the room, match the ambient energy, and decide autonomously when to jump into a conversation, drop a reaction, or completely ignore a prompt. She operates on a multi-model architecture, seamlessly routing requests through various LLM providers (Gemini, Groq, DeepSeek, OpenRouter) to maximize API efficiency on free-tier infrastructure.

## Architecture

The backend is written in Python using `discord.py`. It offloads the computational heavy lifting to external APIs, keeping the bot lightweight. The system is deployed on a Google Cloud Platform e2-micro Linux server, utilizing a systemd daemon to achieve permanent 24/7 uptime without relying on local hardware.

A core feature is her memory management, which has been heavily iterated on. Instead of feeding the whole chat history to the LLM and burning tokens, the bot breathes. Every 25 messages, it slices the overflow and compresses it into a dense running summary via a non-blocking background thread. 

Crucially, the long-term memory (LTM) RAG pipeline has been completely nuked. Experience showed that LTM often acted as a toxic compounding loop, where the bot would anchor too heavily to past behaviors or misinterpretations. LeepaBot now operates completely statelessly across sessions. She relies purely on sharp, immediate contextual awareness and her short-term memory queue. A witty, stateless short-term memory is a massive net bonus over a bloated, misfiring long-term memory.

To prevent chat spam, the bot features an Interception Matrix. If a rival bot answers a message while LeepaBot is still generating a response, the parallel thread flips a kill-switch and aborts text generation. Direct user pings grant an absolute immunity flag to bypass this lock.

## Cognitive Pipeline & Persona

LeepaBot doesn't just generate text; she uses a strict Chain-of-Thought (CoT) Cognitive Pipeline. Every response starts with a hidden `thinking_block`. She analyzes intent, reads the social dynamics, anchors her emotional state, and decides on a comedic angle *before* rendering a single word.

Her personality has been entirely revamped. She used to lean into the "sassy, smug AI" trope, which honestly just felt bitchy and tryhard. Now, she is engineered as a highly enthusiastic, zero-ego, partner-in-crime fox-girl. She acts as an enabler and a peer to the users, jumping into chaotic banter without ever acting superior. 

To break repetitive LLM grooves (like answering questions with rhetorical questions), she operates under a hard structural limitation: **she is strictly forbidden from outputting question marks.** All of her responses are definitive, declarative statements. 

Her persona, rules, and highly curated N-shot examples are decoupled from the routing logic and stored entirely in a separate `prompts.py` file, making behavioral tweaks easy and keeping the `api_handler` completely clean. She also has access to a global, centralized inventory of custom server emojis to deliver punchlines purely through physical reactions when words would ruin the comedic timing.

## Setup Instructions

1. Clone the repository to your target machine.
2. Install the required dependencies using `pip install -r requirements.txt` within a virtual environment. 
3. Create a `.env` file in the root directory to securely house your `DISCORD_TOKEN`, `BAPT_DISCORD_ID`, `OTHER_BOT_ID`, and the relevant API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, etc.).
4. Modify `core/prompts.py` if you want to tweak her baseline persona, N-shots, or add new custom emojis to her arsenal.
5. To achieve permanent uptime on a Linux server, create a systemd service file pointing to your Python executable and the `main.py` script. Enabling and starting this daemon allows the OS to automatically restart the bot in the event of a crash. Future updates are handled by running `git pull`.

## Future Plans

The architecture is functional and fully deployed to the cloud. The next major integration is Phase 5.5, which will give the bot native multi-modal capabilities. This will allow her to "see" uploaded images and read standard text file attachments, seamlessly folding external media into her conversational awareness.
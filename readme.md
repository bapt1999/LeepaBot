# LeepaBot: The Dynamic Conversational AI

LeepaBot is a highly conversational, context-aware Discord AI companion. This project was built as a personal engineering journey focusing on vector mathematics, RAG (Retrieval-Augmented Generation) pipelines, and complex AI behavioral architecture. 

The main idea is that chatbots usually just wait for strict commands and reply blindly. LeepaBot is designed to organically read the room, match the ambient energy, and decide autonomously when to jump into a conversation, drop a reaction, or completely ignore a prompt. She operates on a multi-model architecture, seamlessly routing requests through various LLM providers (Gemini, Groq, DeepSeek, OpenRouter) to maximize API efficiency on free-tier infrastructure.

## The Architecture

The backend is written in Python using `discord.py`. It offloads the computational heavy lifting to external APIs, keeping the bot lightweight. The system is deployed on a Google Cloud Platform e2-micro Linux server, utilizing a systemd daemon to achieve permanent 24/7 uptime without relying on local hardware.

A core feature is her memory management. Instead of feeding the whole chat history to the LLM and burning tokens, the bot breathes. Every 25 messages, it slices the overflow and compresses it into a dense running summary via a non-blocking background thread.

For long-term memory, the bot uses a pure Python RAG pipeline. It calculates the vector embeddings of server lore using Gemini. When a new message comes in, it computes the dot product between the vectors to find the angle and determine semantic closeness. A dynamic half-life exponential decay formula slowly forgets unused lore over time to automatically manage database size.

To prevent chat spam, the bot features an Interception Matrix. If a rival bot answers a message while LeepaBot is still generating a response, the parallel thread flips a kill-switch and aborts text generation. Direct user pings grant an absolute immunity flag to bypass this lock.

Her personality—a bubbly, cheeky, and highly intelligent fox-girl who plays naive to troll users—is managed through a strict markdown-hierarchical system prompt combined with N-Shot behavioral examples. By treating persona constraints as absolute architectural laws, the system prevents the AI from breaking character while guaranteeing she pushes conversations forward.

## Setup Instructions

1. Clone the repository to your target machine.
2. Install the required dependencies using `pip install -r requirements.txt` within a virtual environment. 
3. Create a `.env` file in the root directory to securely house your `DISCORD_TOKEN`, `BAPT_DISCORD_ID`, `OTHER_BOT_ID`, and the relevant API keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, etc.).
4. To achieve permanent uptime on a Linux server, create a systemd service file pointing to your Python executable and the `main.py` script. Enabling and starting this daemon allows the OS to automatically restart the bot in the event of a crash. Future updates are handled by running `git pull`.

## Future Plans

The architecture is stable and fully deployed to the cloud. The next major integration is Phase 5.5, which will give the bot native multi-modal capabilities. This will allow her to "see" images and read file attachments, seamlessly folding external media into her conversational awareness.
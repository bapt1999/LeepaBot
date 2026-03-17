# LeepaBot: The Dynamic Physics Chatbot

LeepaBot is a conversational, context-aware Discord chatbot. The project was built by a first-year particle physics master student with six months of free time and a curiosity for vector mathematics and AI behavioral architecture.

The main idea is that chatbots usually just wait for commands and reply blindly. LeepaBot is designed to organically read the channel, match the ambient energy, and decide for itself if a response is even needed. It operates on a multi-model architecture, routing requests through various LLM providers to maximize API efficiency on free-tier infrastructure.

## The Architecture

The backend is written in Python using discord.py. It offloads the computational heavy lifting to external APIs, keeping the bot lightweight. The system is deployed on a Google Cloud Platform e2-micro Linux server, utilizing a systemd daemon to achieve permanent 24/7 uptime without relying on local hardware.

One of the main features is the memory management. Instead of feeding the whole chat history to the LLM and burning tokens, the bot breathes. Every 25 messages, it compresses the overflow into a dense running summary in a background thread.

For long-term memory, the bot uses a pure Python RAG pipeline. It calculates the vector embeddings of server lore using Gemini. When a new message comes in, it computes the dot product between the vectors to find the angle and determine semantic closeness. There is also a dynamic half-life decay formula that slowly forgets unused lore over time to manage database size.

To prevent chat spam, the bot features an Interception Matrix. If a rival bot answers a message while LeepaBot is still generating a response, the parallel thread flips a kill-switch and aborts the text generation. However, direct user pings grant an absolute immunity flag to bypass this lock.

The bot's personality is managed through a strict markdown-hierarchical system prompt. By treating behavioral constraints as absolute laws rather than suggestions, the architecture prevents the AI from developing an inflated ego while strictly enforcing formatting rules and ensuring it builds upon conversations rather than repeating them.

## Setup Instructions

To deploy the bot to a Linux server, clone the repository to the machine and install the required dependencies using `pip install -r requirements.txt` within a virtual environment. 

A `.env` file must be manually created in the root directory to securely house the Discord bot token, the developer's Discord ID, the rival bot ID, and the API keys. 

To achieve permanent uptime, create a systemd service file pointing to the Python executable and the `main.py` script. Enabling and starting this daemon allows the operating system to take custody of the bot, automatically restarting it in the event of a crash or server reboot. Future updates are handled by pushing code to GitHub and running a `git pull` on the server.

## Future Plans

The architecture is stable and fully deployed to the cloud. The next step will give the bot the ability to parse multimodal attachments, specifically to read physics PDFs and return high-level summaries.
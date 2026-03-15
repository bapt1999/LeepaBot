# LeepaBot: The Dynamic Physics Chatbot

LeepaBot is a conversational, context-aware Discord chatbot.

LeepaBot is designed to organically read the channel, match the ambient energy, and decide for itself if a response is needed. It operates on a multi-model architecture, routing requests through Gemini, Groq, DeepSeek, and OpenRouter, depending on user needs and token usage.

## The Architecture

The backend is written in Python using discord.py. It offloads the computational heavy lifting to external APIs, keeping the bot lightweight enough to run as a cloud router. 

One of the main features is the memory management. Instead of feeding the whole chat history to the LLM and being cost-heavy on tokens, the bot breathes. Every 25 messages, it compresses the overflow into a dense running summary in a background thread.

For long-term memory, the bot uses a pure Python RAG pipeline. It calculates the vector embeddings of server lore using Gemini. When a new message comes in, it computes the dot product between the two vector embeddings to determine semantic closeness. It is an elegant solution that turns linguistics into pure geometry. There is also a dynamic half-life decay formula that slowly forgets unused lore over time to manage database size, while permanently locking in core memories.

## Setup Instructions

To get the bot running locally, a few specific steps are required.

First, clone the repository to the local machine. 
Next, install the required dependencies using the command `pip install -r requirements.txt` in the terminal.
Then, create a `.env` file in the root directory. This needs to include the Discord bot token, the developer's Discord ID, the rival bot ID to prevent infinite looping (if your server has a bot of its own, otherwise disregard), and the API keys for the various LLM providers. A Gemini API is required for embeddings retrieval, otherwise the bot only needs one API key to work through any of the four providers.
Run the bot by executing main.py.

## Future Plans

The architecture is stable, but Phase 5 is currently pending. This next step will give the bot the ability to parse multimodal attachments, specifically to read physics PDFs and return high-level summaries. The bot will eventually be cloud-hosted. This version is locally ran.
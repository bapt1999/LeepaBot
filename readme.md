# LeepaBot

Leepa is a Discord chatbot built to participate in a server's conversations: talk, joke, react, sometimes stay quiet, and use tools when they help. She has a persistent notebook, web search, a small Python execution environment, and tools for looking up Discord messages and pins.

She is a character, but her character should not replace engagement with what people actually said. Her personality and relationships are maintained by the owner in `core/prompts.py`.

Documentation updated **15 November 2026**. This describes the latest implementation supplied in this work session and the owner's reported tests. It is not a fresh export of the running VM. User-maintained prompt text, switches, bot IDs, channel lists, installed packages, and deployment settings remain authoritative in the actual project. Model names below are configuration values, not a new verification of provider availability.

For the decisions, earlier implementations, rejected approaches, and incidents, see `Objective and context.txt`. `LeepaBot_revamp_plan.txt` records the status of the original eight steps.

## What Leepa can do

- Answer when mentioned, named, or replied to, subject to the bot-specific protections below; occasionally join ambient conversation.
- Produce text, an emoji reaction, both, or deliberate silence.
- Receive reply references, server/channel/thread information, display names, bot identity, and attachment facts.
- Describe supported image attachments through a separate vision model.
- See message edits, including Sloppy's queued messages changing into completed results.
- Use Sloppy creation commands in servers where Sloppy is enabled.
- Read and maintain her own persistent text notes.
- Search the public web and use returned sources.
- Execute small Python programs and attach a validated PNG graph.
- Observe reactions to her own messages without automatically replying to them.
- Search Discord messages, read surrounding history, retrieve an individual message, and read pins in permitted channels.

Most tools use ordinary conversation, not a special user command. Leepa may also use them on her own. The selector can require relevant tool use; the main model performs it.

## Public commands

`!Leepa` asks for help. Its prompt is assembled from the features available in the current location. It works independently of Sloppy. Web/Python descriptions follow their flags, and Sloppy commands are omitted where Sloppy is unavailable. Notebook instructions are private guidance for Leepa, not part of the public help text. The latest help blocks do not yet explicitly advertise the new Discord-history tools.

The known Sloppy command catalog from this session is below. The current `SLOPPY_MODE_TRIGGERS` and help-description dictionary in `prompts.py` determine the actual list.

| User command | Leepa's outgoing command | Purpose |
| --- | --- | --- |
| `!Lh3` | `!h3` | Video |
| `!Lhanime` | `!hanime` | Anime-style video |
| `!Lkrea` | `!krea` | Image using Krea |
| `!Lkroma` | `!kroma` | Image using Kroma |
| `!Lwulv` | `!wulv` | Image using Wulv |
| `!Lqwen` | `!qwen` | Image using Qwen; aspect ratio/transparency options |
| `!Lyue` | `!yue` | Song instructions and lyrics |

Commands are case-sensitive and start the user's message. Describe the idea after the command in ordinary language. Leepa develops the actual creation prompt and decides details such as duration; these are not rigid argument parsers for users.

Sloppy recognizes its command only at the literal start of Leepa's outgoing message. A preamble, code fence, or backtick before it prevents invocation. For a dedicated Leepa command, the prompt requires the corresponding Sloppy prefix at the start of `response`. Leepa may also invoke Sloppy organically. There is no separate `sloppy` JSON field and no post-generation rewriting to move a command into place.

For `!h3`, duration is 1–15 seconds, default 5. `!hanime` follows the same form. For `!yue`, song/style instructions go on the first line and lyrics start after a newline; verse/chorus tags and timestamps can be useful. Timestamps were also documented for video prompts. The creator's note that an instrumental Yue request can reach 6 minutes 20 seconds is historical service guidance, not a guarantee enforced by Leepa.

Leepa does not attach source images or audio to Sloppy. `!qwedit`, `!ycov`, `!yext`, and `!yflow` were excluded from her command routes because they require uploaded source media. Sloppy commands and service descriptions are maintained manually as Sloppy changes.

## How a response is built

1. `main.py` receives Discord events and forwards them to the logic layer.
2. `logic.py` collects attachment information, maintains the channel's STM, resolves the current Discord context, and decides whether to start a response turn.
3. The prompt selector receives recent history, its running summary, the current Discord event, the assembly catalog, and available capabilities. It selects a prompt assembly and any required actions.
4. `prompt_builder.py` combines the owner's static prompt, the selected assembly, location-specific features, any relevant bot relationship, tool guidance, and the output contract. The current incoming message is separate from earlier chat history.
5. The main model answers or requests tools. Tool results return to the same response loop, allowing follow-up calls before the final answer.
6. The final JSON is parsed/repaired as needed. `logic.py` applies the reaction, sends the reply and any validated graph, and records the outcome in STM.

Edits and reaction feedback update context without becoming new response triggers. Sloppy output is remembered but does not start a conversation.

### Selection and output

The selector chooses among the configured assemblies; it does not write Leepa's reply, invent arbitrary prompt text, or execute tools. The earlier ambiguous `serious`/`grounded` distinction was revisited with clearer descriptions. A supplied replacement catalog used `focused`, `casual`, `playful`, and `absurd`; the owner's current catalog determines the actual names and text.

The selector's JSON contains `assembly_id`, `required_tools`, `required_output`, `notebook_action`, `task`, and `reason`. Required-tool booleans cover Python, web search, notebook operations, and Discord history. True requires work; false leaves the tool optional. Required PNG output is tracked separately. A preparatory notebook listing does not satisfy a required write, and a channel listing does not satisfy a required Discord-message lookup. Tool failures do not count as success.

The main model's final JSON always includes `internal_mood` and `response`. `thinking_block` and `reaction_emoji` follow their switches. This application-level thinking field is generated text, not access to a model's private internal reasoning. `internal_mood` is retained in STM; it is not a numeric mood controller or a separate automatic learning system.

The selector has its own fallback chain, fixed temperature, strict validation, bounded retries, and one-correction handling. If it cannot produce a valid selection, the default assembly is used with tools still optional and an explicit notice that selection failed. The main model can still assess tool needs.

## Configuration

### Feature switches: `core/prompts.py`

| Setting | Meaning |
| --- | --- |
| `USE_N_SHOTS` | Include available examples when their required optional features are enabled |
| `USE_THINKING_BLOCK` | Include the application thinking field and its instructions |
| `USE_SEED_WORD` | Enable seed words when the thinking block is enabled |
| `USE_EMOJI_REACTIONS` | Allow the reaction field and physical reactions |
| `USE_CUSTOM_EMOJIS` | Allow custom emojis when reactions are enabled |
| `USE_TEMPERATURE_JITTER` | Randomize the configured temperature selection |
| `FIXED_THERMAL_SCALAR` | Default scalar for calls without an assembly when jitter is disabled |
| `USE_PROMPT_ASSEMBLER` | Use the separate selector |
| `USE_SLOPPY` | Enable Sloppy integration, also subject to server configuration |
| `USE_NOTEBOOK`, `NOTEBOOK_WRITES`, `NOTEBOOK_DMS` | Enable notes, writes, and optional DM scopes |
| `USE_WEB_SEARCH` | Offer provider-hosted search |
| `USE_PYTHON`, `USE_PYTHON_IMAGES` | Offer Python and optional PNG collection |
| `USE_REACTION_FEEDBACK` | Observe/display reactions to Leepa's messages |
| `USE_DISCORD_HISTORY` | Offer Discord lookup tools |

The table lists controls, not a prescribed set of values. Preserve the owner's current choices and removed examples. Turning jitter off does not omit the temperature parameter: assembly temperatures use the range midpoint; other calls use the fixed scalar. Seed words cannot remain active when the thinking block is disabled.

Custom emoji descriptions are optional entries in `EMOJI_DESCRIPTIONS`, keyed by the exact emoji string used in `AVAILABLE_EMOJIS`. Unannotated emojis remain usable. The no-consecutive-repeat request is prompt guidance, not a Python ban. Sarcasm-only meanings and matched sets belong in the editable descriptions.

### Secrets and deployment values: `.env`

Keep `.env` out of Git. It contains credentials and deployment identifiers/paths, not the feature on/off switches above.

- `DISCORD_TOKEN` and `BAPT_DISCORD_ID`.
- `OPENROUTER_API_KEY`, `SILICONFLOW_API_KEY`, and `GEMINI_API_KEY` for the configured chat/search, fallback, and vision services.
- `RAKUN_ID`, `SLOPPY_ID`, `BUNS_ID`, `WARRO_ID`, `GRINCH_ID` for the configured bot catalog. `OTHER_BOT_ID` was replaced by `RAKUN_ID`.
- `SLOPPY_SERVER_IDS`: comma-separated server IDs where Sloppy is available.
- `NOTEBOOK_ROOT`, normally an absolute path to `~/Leepa_sb`; `NOTEBOOK_SERVER_IDS`; optional `NOTEBOOK_SHARED_CHANNEL_IDS`.
- `LOG_LEVEL`, normally `INFO` to see incoming/outgoing messages and tool activity.

Unused provider entries may still exist in the API module. Their presence does not mean their API keys are needed by the active chain. Do not copy old `.env` feature switches back into the active configuration.

The owner reported adding Grinch to the deployed registry and `.env`; the builder includes his prompt block. The older saved `bot_registry.py` copy in this session predates that manual addition and must not replace the owner's newer registry.

### Server/channel separation

`server_config.py` controls Sloppy availability and notebook scope. Unknown servers default to Sloppy unavailable. `!Leepa` still works there.

Notebook locations are scoped by server and channel; configured shared channels use the server's shared notebook. Threads have separate scopes. DMs are separate and require `NOTEBOOK_DMS`. An enabled notebook with no enabled server IDs is still unavailable in servers.

`DISCORD_HISTORY_CHANNELS` is a mapping of server IDs to additional readable channel/thread IDs in `server_config.py`. The current server channel/thread is allowed by default, subject to Discord permissions. Adding another channel explicitly permits its contents to be consulted from other conversations in that server. Threads need their own IDs; allowing a parent does not grant all its threads. Cross-server lookup is blocked; the new history tools do not expose DMs.

## Models and temperature

The latest saved main response chain is:

1. `deepseek_openrouter`: `deepseek/deepseek-chat-v3-0324` through OpenRouter.
2. `deepseek_v41_siliconflow`: `deepseek-ai/DeepSeek-V4.1-Flash` through SiliconFlow.
3. `deepseek_v41_openrouter`: `deepseek/deepseek-v4.1-flash` through OpenRouter.

The selector has a separate route list using those model families in the same broad order, with its own request settings. JSON repair uses a dedicated DeepSeek V3 formatter profile. STM summarization uses the main fallback chain with a fixed normalized scalar of 0.10; that still passes through the selected profile's temperature scaling. The selector sends temperature 0.10 directly; the formatter's configured scalar is 0.05.

The main V3 OpenRouter profile scales normalized temperature by 1.4. The supplied V4.1 profiles use scale 1.0 and cap temperature at 1.0, with provider-specific sampling settings. These choices followed an incoherent-output incident after a model/provider change. Temperature values are not assumed equivalent across providers.

The `deepseek_siliconflow` and `deepseek_v4_flash` definitions and generic Gemini/Groq/provider branches remain in the file but are not in the active response chain. Do not describe the bot as switching between Gemini and Groq through one master switch. The Gemini chat fallback was removed from the active chain.

Vision is separately configured in `image_analysis.py`: the supplied replacement names are `gemini-3.5-flash-lite`, then OpenRouter `meta-llama/llama-4-scout`. The older Gemini 2.5 Flash and Llama 3.2 11B vision routes were superseded. This records the chosen configuration; provider availability needs its own audit when changing routes.

## Memory, Discord context, and bot interactions

### Short-term memory

Each channel/thread has its own in-memory queue and running summary. The supplied queue uses a base size of 20 and removes a 15-entry chunk for summarization when it reaches 35 entries. It is not a timer that saves a memory every 25 or 50 messages. Leepa's own response/state entries also enter this queue.

Message IDs allow edits to update the matching entry. Older tool-bot edits may be added when the original entry has left STM. Ordinary older edits are not automatically reinserted. Image descriptions enter memory even for messages that do not trigger a reply. PNG, JPEG, and WebP are supported; other attachments are recorded as metadata. Videos and songs are not automatically watched or heard.

Replies can resolve older referenced messages outside STM. A fetched reply to Leepa also counts as `QUOTED` for engagement. Reply references and actual Discord threads are different, and both are represented. People are referred to by `display_name`, or a shorter version of it; IDs are stable markers for code and tool arguments.

### Engagement and bot protections

`DIRECT`, `QUOTED`, and `AMBIENT` are private call-trigger categories, not semantic labels attached to incoming messages. The model receives the content and context to interpret.

In the saved logic, ambient probability is 5%; Rakun's ambient multiplier is 0.3, giving 1.5%. Direct/quoted human engagement bypasses the ambient roll. These numbers are tunable code values, not immutable personality rules.

After a successful reply to a chatbot, an 80% roll starts a 60-second pause for that bot in that channel. The remaining 20% leaves another exchange possible. There is no current fixed four-reply chain cap. This is a pause mechanism, not an absolute bound on the number of exchanges.

The separate concurrent-reply cancellation remains: a chatbot replying first to the same triggering message can cancel a pending ambient turn. Direct/quoted turns are immune. Merely attaching an image is not the current immunity rule. One bot-originated response is processed at a time per channel. These protections reduce spam but do not guarantee that two independent bots will never produce split branches.

Rakun, Buns, Warro, and Grinch have separate identity/relationship context. Rakun's tsukkomi/boke instructions are conditional on his ID. Bot pause protection uses the author's bot flag, not only a matching Rakun ID. Sloppy is classified as a non-conversational tool bot; its output is remembered without triggering a reply, even when it replies directly to Leepa's own invocation.

### Persistent notebook

The notebook replaces the old automatic embedding/decay LTM. Leepa can list, search, read, write, append, move, delete, inspect versions, and restore `.md`/`.txt` notes. Listing/search lets her discover filenames dynamically. Search is literal text/filename search, not embeddings.

An existing file must be read before modification and written with its current revision. New files use revision `missing`. Writes are atomic and versioned; deletion is recoverable through saved history. Paths are confined to the selected notebook scope, with link/special-file checks and quotas. The root must be owned by the bot account with mode 700. The saved limits are 32 KiB per note, 64 MiB total notebook storage, and 5,000 filesystem entries, including bookkeeping/history.

Notes persist across restarts; STM and the running summary do not. Routine note use can remain private and need not generate a public reply. Nothing automatically writes a permanent memory every 50 messages, promotes a core memory, applies time decay, or rewrites the system prompt from notebook contents.

### Reaction feedback and Discord retrieval

Raw reaction add/remove/clear events can resolve older uncached Leepa messages. They retain the reactor's display name, emoji identity, original-message excerpt/date/link, location, and observed action. Leepa's own reactions are ignored; other bots can appear as identified reactors.

Feedback is separate from ordinary STM messages and never triggers an LLM call by itself. The implementation keeps up to six target messages, eight latest observed actions per target, a 24-hour event age limit, and a bounded prompt section. Repeated add/remove activity is coalesced. These are recent observations, not a complete live reaction roster or permanent training. Events missed while offline are not reconstructed automatically.

Discord tools are `list_discord_channels`, `search_discord_messages`, `read_discord_history`, `read_discord_message`, and `read_discord_pins`. They run through the existing Discord client, not Piston. Results include message IDs/links, names, dates, reply references, attachment facts, and available reaction information. Search results omit reactions; a message fetch can retrieve them. Pins/history have pagination, and lookup failures are distinct from an empty result. Retrieved messages remain tool results, not newly arrived STM messages. A useful finding can separately be saved as a note.

## Web and Python tools

Web search uses OpenRouter's provider-hosted `openrouter:web_search` tool, configured with Exa. No separate search API key or approved-domain list was added. It returns source URLs and excerpts; it is not a general browser. The main answering model chooses and uses the search. The selector can require it, but does not do the research itself. A direct SiliconFlow request does not offer this hosted search; pending required search routes through a compatible OpenRouter profile. Available capabilities and budgets are communicated per request.

Python runs through self-hosted Piston at `http://127.0.0.1:2000/api/v2/execute`, on the same VM. Piston supports other runtimes, but Leepa currently exposes only Python 3.12.0. Neither arbitrary shell execution nor automatic package installation is a tool.

The runtime inventory printed during setup included NumPy, pandas, SciPy, SymPy, mpmath, and various utility/cryptography packages. Matplotlib and Pillow were absent from that original printout. The subsequent plotting revision added guidance and a sanity check for a Matplotlib/Pillow-capable setup; the owner later reported the broader changes working. Verify the actual runtime before relying on a package. Host-venv packages and Piston-runtime packages are separate; the bot also needs Pillow to validate collected PNGs.

`piston_job.py` is sent as the job entry point. It runs user code in a temporary working directory, captures bounded stdout/stderr, and packages a requested PNG into the result. `code_runner.py` validates/decodes that image before Discord publication. There is no general host output-directory mount that publishes everything a program writes.

Only the requested PNG can be queued, and it is attached with the final response. A new Python run replaces the previous pending graph, including when it fails. Code errors, invalid PNGs, cancellation, and failed publication must not be presented as successful output. A graph is not attached to an outgoing Sloppy command. Text output goes back to the model; code files and arbitrary attachments are not published automatically. Durable conclusions require notebook writes.

Saved execution limits: one worker job at a time; two runs per response turn; 12,000 source bytes; 128 MiB per job; 5 CPU seconds; about 8 seconds in the wrapper with a 10-second Piston limit; 4,096 bytes per stdout/stderr stream; one non-animated PNG up to 256 KiB and 1024 by 1024 pixels. The supplied Compose configuration caps the container at 256 MiB, one CPU, and 128 PIDs, binds the API to loopback, and disables networking for executed jobs. It uses a privileged Piston container: this is not a separate-VM security boundary or a guarantee that host compromise is impossible. No bot-code or notebook folder is deliberately mounted into jobs.

The shared turn budget is 90 seconds and at most ten model requests. Notebook actions and Discord lookups each have a six-call limit; web search allows two searches with bounded excerpts; Python allows two runs. Required-action reminders are also bounded. These are operational controls, not prompt-based limits alone.

## Files and operation

| File | Responsibility |
| --- | --- |
| `main.py` | Discord connection, logging setup, event forwarding |
| `core/logic.py` | Message flow, STM updates, engagement, cancellation, bot pauses, Discord output |
| `core/prompts.py` | Owner-maintained persona, assemblies, tool guidance, flags, emojis and command catalog |
| `core/prompt_builder.py` | Prompt assembly, conditional bot/context blocks, help and output contract |
| `core/api_handler.py` | Model profiles, selector chain, sampling, JSON recovery, tool continuations, summaries |
| `core/discord_context.py` | Current event facts and referenced-message resolution |
| `core/server_config.py` | Server features, notebook scopes, additional readable Discord channels |
| `core/bot_registry.py` | Known bot IDs and non-conversational bot classification |
| `core/memory_queue.py` | Channel STM, summary overflow, bounded reaction feedback |
| `core/notebook.py` | Persistent scoped files, revisions, versions and recovery |
| `core/tools.py` | Tool definitions/dispatch, budgets and required-action tracking |
| `core/discord_history.py` | Discord history/search/pins and reaction-target resolution |
| `core/code_runner.py` | Piston requests, execution limits and PNG validation |
| `core/piston_job.py` | Temporary job wrapper executed inside Piston |
| `core/image_analysis.py` | Image download and independent vision routing |

The established VM layout is `~/LeepaBot` for application code, `~/Leepa_sb` for persistent notes, and `~/Leepa_exec` for the Piston deployment. These are folders on one VM, not separate SSH instances. The host reported Ubuntu 24.04, x86-64, cgroup v2, about 955 MiB RAM, and no swap during setup. Treat that as a recorded measurement, not a live specification.

Leepa runs through `leepabot.service`, using the project's virtualenv Python. Notebook ownership/permissions and directory paths must match the account running that service. Docker/Piston is managed separately.

For a fresh checkout, create a Python virtual environment, install the repository's dependency list, supply `.env`, and configure the flags and server IDs before starting the bot. The current modules require `discord.py`, `python-dotenv`, `httpx`, and Pillow in the bot environment; use the project's maintained dependency versions rather than treating this short list as a lockfile. Python 3.12 matches the reported VM environment. Enable Message Content in the Discord application settings as well as in code, and grant the bot the channel permissions its configured features need. Notebook and Piston setup are separate from installing the bot's Python dependencies. The existing service's `WorkingDirectory` and `ExecStart` should point to this checkout and virtualenv.

Useful read-only checks:

```bash
sudo systemctl status leepabot.service --no-pager
sudo journalctl -u leepabot.service -f -n 100 -o cat
sudo systemctl show leepabot.service --no-pager -p WorkingDirectory -p ExecStart -p StandardOutput -p StandardError
```

For Piston:

```bash
cd ~/Leepa_exec
sudo docker compose ps
sudo docker stats --no-stream leepa-piston
curl --fail-with-body --silent --show-error http://127.0.0.1:2000/api/v2/runtimes
```

Set application logging to `INFO` to see `>>> INCOMING`, `<<< OUTGOING`, `[SAMPLING]`, `[PROMPT ASSEMBLER]`, `[NOTEBOOK]`, `[WEB]`, `[CODE]`, `[DISCORD]`, and `[REACTION]` as applicable. Logs can contain message text, generated thought/mood fields, tool arguments, and code; they are diagnostic output, not an exact reconstruction of every prompt or a full persistent memory database.

The owner keeps code on the desktop, GitHub, and VM. Keep those copies synchronized deliberately, including any channel-ID edits. `.env`, notebook data/history, and the Piston runtime installation are separate deployment state and are not backed up merely by committing application code. The one-off `install_discord_update.py` was a migration aid, not a runtime dependency or the ongoing delivery method. Full source replacements were subsequently supplied; the owner intends to remove the installer.

## Known limits and unfinished items

- Image analysis is awaited before a message enters STM; overlapping events can still be recorded out of arrival order.
- Summary extraction removes a chunk before the summary request succeeds. The current failure path does not restore that chunk. This remains an identified issue, not a completed fix.
- Lock lifetimes were extended for tool turns and cancellation propagation was repaired. The no-tool path still has a 60-second lock lifetime; slow fallback timing is not universally solved.
- The hard-coded 1,800-character help instruction was removed. The saved API still has a 1,000-output-token ceiling, and the send path still uses a single Discord reply without a general splitter. Longer-answer delivery is not complete merely because prompt wording changed.
- Notes exist, but deciding what to retain and avoiding personality drift remain behavioral work. No claim is made that tools or a selector have eliminated imitation, repetitive emoji choices, or self-focused roleplay.
- Public help has not yet been extended to describe Discord-history lookup explicitly.
- Package inventories, model availability, manually edited prompts, and deployed Grinch/channel configuration need to be checked against the actual deployment before further code rewrites.
- PDF ingestion, additional languages, autonomous dependency installation, broad browser/page-fetch access, separate research agents, scheduled notebook reviews, and bot self-modification remain outside the implemented feature set.

## References

- [Piston project and API documentation](https://github.com/engineer-man/piston)
- [Discord message, search, history and pins API](https://docs.discord.com/developers/resources/message)
- [discord.py API reference, including raw reaction events](https://discordpy.readthedocs.io/en/stable/api.html)

These references explain the underlying services. This project's code and the owner's explicit decisions determine what Leepa is actually allowed and configured to do.

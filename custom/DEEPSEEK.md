# Official DeepSeek API

Open the sidebar **DeepSeek · подключить API**, create a personal key at https://platform.deepseek.com/api_keys, paste it in the password field, then click **Проверить и подключить**. The check uses only `GET /models` and `GET /user/balance`; it does not generate paid text. Refresh the model list after success. Harness is not required. Saving without testing keeps the key private but does not activate models.

The current official API advertises `deepseek-flash` (DeepSeek-V4.1-Flash) and `deepseek-v4-pro`. Discovery uses machine metadata rather than a fixed legacy pair. The retired `deepseek-chat` and `deepseek-reasoner` aliases are not registered. Each discovered compatible model offers a **Chat** preset (explicit thinking disabled), and a **DeepThink** preset when supported. A local `:deepthink` suffix selects the mode; the real API receives only the actual model ID and documented thinking/effort parameters.

Keys stay in the owner's private connection store, not in browser local storage, URLs, logs or this repository. Native Open WebUI model presets have an owner-only read grant, and the bridge independently validates the selected model against the authenticated owner's connection. Plain chat, Harness and independent parallel requests use the same private profile.

This integration currently provides **text conversation, context, streaming, durable provider reasoning and a separate final answer**. It does not claim that the common API worker can analyze original uploaded images/files or execute tool calls, even if a DeepSeek model itself advertises such capabilities. Paste extracted document text into the prompt when needed; CLI agents remain a separate execution mode.

DeepSeek reasoning is stored in its own event channel and a bounded 64 KiB saved field, rendered as an escaped collapsible provider section, and excluded from subsequent conversation history. Cancellation also interrupts an HTTP request before its first token, without automatically resending the completion.

The money display uses the actual `/user/balance` currency (USD or CNY). CNY is never silently relabelled as USD. The provider does not return cumulative spend there; unknown spend remains unknown, not zero. A key may be valid while the account has no generation credit. API usage follows DeepSeek billing; installing the adapter does not include a key or free credit.

Official references, checked 2026-11-05:
- https://api-docs.deepseek.com/api/list-models/
- https://api-docs.deepseek.com/guides/thinking_mode/
- https://api-docs.deepseek.com/api/get-user-balance/
- https://api-docs.deepseek.com/api/create-chat-completion/

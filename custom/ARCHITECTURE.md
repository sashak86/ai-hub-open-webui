# Architecture

Open WebUI retains its native sign-in, conversations, file records, preview UI, model list, and streaming events. Two pinned compiled chunks add stable pending-request IDs and initialize the compact workbench controls. Additional JavaScript uses same-origin authenticated APIs.

The standalone host service validates each browser's native Open WebUI JWT through the native auth endpoint. It computes the owner hash itself and forwards only that identity plus a private bridge bearer to the job API. Client-supplied owner headers are discarded. The private bearer is never sent to browser code. Only administrators and explicit allowlisted users may configure the workbench.

The durable queue stores separate owner/chat/job descriptors and append-only event streams. Native CLI children are detached from HTTP request lifetimes. File locks serialize a conversation and stable message IDs prevent duplicate acceptance. Cancellation, reload, and provider interruption do not silently submit a second copy of the same turn. Harness policies add a persisted plan/checkpoint/memory layer and authorized model/extension selection.

Workspace registry and storage credentials are private to the computed owner. Filesystem operations are confined to managed directories and explicitly configured local roots. Relative path validation rejects traversal and symlink escapes. Scheduled backups use durable registry state, bounded CPU-friendly ZIP compression, content hashes, and pre-restore rollback copies. WebDAV requests keep credentials on the server and use bounded response reads.

Public source contains only portable chat modules and minimal host adapters. It does not contain the author's larger private control-panel repository, server credentials, device configuration, logs, chat data, or camera content.

# Independent parallel requests

Use the compact button with two opposite arrows beside Harness. It remains available while the native conversation is responding. Enter an independent request and select its model.

Each request has a new native session and private working directory. Only the entered text is context: the original chat history, project and unfinished files are not copied. Owner-scoped tools remain available according to the selected provider. The working directory is not a full access sandbox. Agents receive inherited AGENTS.md/CLAUDE.md rules and must create their own git worktree/branch before editing an existing repository concurrently. Harness project selection is not inherited, and addon memory uses the new request scope.

Two workers may execute simultaneously across all chats, including the main chat; additional requests wait. Existing per-conversation locking and serialized local image/video generation are unchanged. Every request has its own status, progress, result, timing and cancellation. The list and results are saved on the server below SASHA_WORKBENCH_DATA/chat-jobs; reload does not restart a turn.

A client request UUID is retained per authenticated browser account until acknowledgement. A lost HTTP response reuses that UUID; server file locking and deterministic job IDs prevent duplicate execution. A failed or interrupted running turn is never blindly replayed.

Accepted: real Codex completed, neighboring request cancelled, reload and nonce replay without duplicate jobs; native active composer320/390 stays one row with Stop available, zero JS errors. Offline tests cover actual worker concurrency/cancellation and browser races/account switching. This feature does not remove provider quotas or make API calls free.

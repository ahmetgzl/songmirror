# Transfer history

Open Transfers and select **Transfer history** to review copy attempts, including failed and stopped transfers. Search either playlist name or ID, filter by status, source or destination account, service, and an inclusive date range, then select **Apply filters**. Records appear newest first; **Clear** resets the filters.

**View details** shows the source and destination, confirmed additions, unavailable entries, unmatched tracks, timestamps, any failure message, and a trail of status changes and control requests. **View transfer** opens its progress display. Live jobs retain their existing controls and conflict review; records loaded after a restart are read-only.

History is stored in `transfer_history.db` under the application data directory (`SONGMIRROR_DATA_DIR`, normally `./data` locally or `/data` in Docker). Keep that directory mounted to preserve history when replacing a container. No additional volume is needed. History is retained until the data directory is removed; no automatic expiration is applied.

Each record includes public job metadata and its conflict results. Credentials, runtime account configuration, and the private source-track snapshot are excluded. Progress is checkpointed at most once per second, with immediate saves for status changes, controls, and completion. An abrupt process exit can lose progress since the last checkpoint; a server restart marks unfinished jobs as interrupted and retains their last confirmed count. Start another copy to continue; destination deduplication skips tracks already present.

History starts with this version. Earlier attempts that existed only in a previous process's memory cannot be reconstructed automatically.

# Sending finished records to Hermes

The "YouTube video" recipe diarizes the transcript and writes
`transcript.speakers.json` / `.md` into the record's folder
(`<data dir>/output/<name>-<record id>/`). `tools/hermes_push.py` copies
such folders to the server over SSH — Hermes never polls this machine.

## Setup

1. Key-based SSH to the server must work without a prompt
   (`ssh user@host true`).
2. In `config.json` set `"hermes_push_target": "user@host:/srv/hermes/inbox"`
   (empty = off).
3. `.venv/bin/python tools/hermes_push.py --dry-run`, then without the flag.

Each folder lands in `<inbox>/<name>-<id>/`: `transcript.speakers.json`,
`transcript.speakers.md`, `youtube_package.json` and `youtube_upload.json`
(when they exist), and `ready.json` **last**. Re-running sends nothing until
the transcript or YouTube package changes (state: `hermes_push.json` in the
folder). `--force` sends everything again.

## Run it automatically (macOS)

`~/Library/LaunchAgents/com.whispered.hermes-push.plist`, then
`launchctl load` it. It runs every 15 minutes; if the laptop was asleep or
offline the next run catches up.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.whispered.hermes-push</string>
  <key>ProgramArguments</key><array>
    <string>/Users/den/Projects/03 Whispered/.venv/bin/python</string>
    <string>/Users/den/Projects/03 Whispered/tools/hermes_push.py</string>
  </array>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>/tmp/hermes-push.log</string>
  <key>StandardErrorPath</key><string>/tmp/hermes-push.log</string>
</dict></plist>
```

## Server side (no LLM, no tokens)

Watch for `ready.json` with a plain service and ingest the folder it sits in:

```bash
inotifywait -m -r -e close_write,moved_to --include 'ready\.json$' \
    --format '%w' /srv/hermes/inbox | while read dir; do
  /srv/hermes/ingest.sh "$dir"   # reads ready.json: files, digest
done
```

Run it as a `systemd` service. `ready.json` arrives after all other files, so
the folder is complete when it appears; `digest` lets the script skip a
folder it already ingested.

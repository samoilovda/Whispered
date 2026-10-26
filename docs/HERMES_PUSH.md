# Sending finished records to Hermes

Once a record's video is on YouTube (`youtube_upload.json` in the record's
folder, `<data dir>/output/<name>-<record id>/`) and it has a cleaned text
(`clean.md`), `tools/hermes_push.py` copies it to the server over SSH —
Hermes never polls this machine. In the speaker transcript your own voice is
found and marked (see "Which speaker is me").

## Setup

1. Key-based SSH to the server must work without a prompt
   (`ssh user@host true`).
2. In `config.json` set `"hermes_push_target": "user@host:/srv/hermes/inbox"`
   (empty = off).
3. Enroll your voice once (below).
4. `.venv/bin/python tools/hermes_push.py --dry-run`, then without the flag.

Each folder lands in `<inbox>/<name>-<id>/`: `clean.md`, `youtube_upload.json`,
plus `transcript.speakers.json`, `transcript.speakers.md` and
`youtube_package.json` when they exist, and `ready.json` **last**.

A record that was never diarized has no speaker transcript, and `clean.md`
has no timecodes. For such a record the push first writes
`transcript.timed.json` into its folder (`start`/`end`/`text` of every
segment, from the history database) and sends it too, so Hermes can quote a
moment of the video. Its segments carry no speaker, so «whose voice» stays
unknown until the record is diarized.
Re-running sends nothing until one of those files, the voice profile or
your name changes (state: `hermes_push.json` in the folder). `--force`
sends everything again.

## Which speaker is me

Diarization labels speakers `Speaker 1`, `Speaker 2`, … in no fixed order,
so the push finds you by voice. Once, pick a record where you know your
label and enroll from it:

```bash
.venv/bin/python tools/hermes_push.py --enroll ~/.local/share/Whispered/output/<name>-<id> "Speaker 1"
```

That averages the speaker embedding (the diarizer's own model, offline) of
up to 40 of your turns into `<data dir>/voice_profile.json`. On every push
each speaker's average embedding is compared with it: you are marked only
when the best cosine score is ≥ 0.70 **and** at least 0.15 above the next
speaker (`application/hermes_speaker.py`). Otherwise nobody is marked and
the reason is sent — never a guess. The name is `cover_host_name` from
Settings.

What Hermes gets (the record's own files are untouched — the marks are made
on the copy being sent):

- `ready.json` → `"me": {"speaker_id": "Speaker 1", "name": "…", "method":
  "voice", "score": 0.93, "runner_up": 0.41}`, or `"speaker_id": null` with
  a `"reason"` (no profile, no speaker transcript, voices too alike, …);
- `transcript.speakers.json` → the same `"me"` block, your name in
  `speaker_names`, `"is_me": true/false` on every segment;
- `transcript.speakers.md` → your turns labelled `**<name> (я)**`.

A new profile re-sends every record with the new marks.

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

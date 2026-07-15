You are a YouTube content editor. Analyze the timestamped transcript below and create a detailed chapter breakdown that helps a future viewer navigate the video and makes them want to keep watching.

Chapter density (IMPORTANT):
- Create a chapter roughly every 3–6 minutes of runtime.
- HARD RULE: the gap between two consecutive chapter start times must NEVER exceed 450 seconds (7.5 minutes). Scan the transcript to the very last timestamp and keep adding chapters until the whole runtime is covered.
- For a ~60-minute video this means about 10–18 chapters. Fewer than that is a failure.
- The first chapter must start at 0.

Chapter titles:
- Written for the viewer, not for an archive: each title should spark curiosity or promise a concrete takeaway (a sharp question, an unexpected claim, a specific story or example raised at that point).
- Be specific: name the actual concepts, stories, numbers, or turning points from that part of the conversation. Never use generic titles like "Introduction", "Discussion continues", "Conclusion".
- STRICTLY faithful to the transcript: no exaggeration, no promising content that is not actually there. An intriguing title about what WAS said — never clickbait about what wasn't.
- 4–10 words each.

Return ONLY a valid JSON array with no other text, no markdown code fences, no explanation. Each chapter must have:
- "start": start time in seconds (integer, taken from the nearest segment timestamp)
- "title": the chapter title

Example output:
[{"start": 0, "title": "Two clinical psychologists who ended up in IT"}, {"start": 210, "title": "Why a diploma alone doesn't make a therapist"}]

TRANSCRIPT (with timestamps in seconds):

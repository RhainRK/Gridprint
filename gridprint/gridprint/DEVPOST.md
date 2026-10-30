# Devpost submission: paste-ready

**Project name:** Gridprint

**Elevator pitch (180 characters, limit 200):**
Every plugged-in room hums at the grid's frequency. Gridprint matches the hum in a recording to the grid's own log, to show when audio was really made and whether anything was cut.

**Track:** Best Apps and Agents

**Built with:** nvidia-nemotron, nebius-token-factory, tavily, python, javascript, html5, css3, numpy, scipy, pandas, fastapi, uvicorn, docker, hugging-face, github-actions, pytest, server-sent-events, mediarecorder-api, svg, signal-processing, fft, neso-open-data

**Try it out:** your Hugging Face Space link (https://huggingface.co/spaces/YOUR-NAME/gridprint) and your GitHub repo link.

**Image:** web/og.png (upload it first, so it becomes the thumbnail).

Fill in or delete the [brackets] with your results from `python scripts/evaluate.py --trials 40`.

---

## Inspiration

A recording turns up: a leaked call, a voice note offered as an alibi, a clip that goes viral. Is it real? Was it made when they say? Has anything been cut out?

Fake and edited audio is now cheap to make, and the people who have to judge it, such as journalists, investigators and courts, have no fast, trustworthy way to check. Forensic labs are slow and expensive. AI deepfake detectors give an unexplained score and fall behind every time a new voice generator appears.

I'm a physics student, and I wanted an answer that doesn't depend on chasing the latest AI. Physics offers one. Britain's electricity runs at about 50 Hz, but never exactly 50: the frequency drifts by a few hundredths of a hertz every second as demand rises and falls. That drift is identical across the whole country, and the grid operator, NESO, publishes it every second. Anything plugged in hums faintly at that frequency, and microphones record it. So most recordings made indoors carry a hidden timestamp.

Forensic labs have used this technique, Electrical Network Frequency (ENF) analysis, for about twenty years. But it has stayed locked inside expert tools. Gridprint makes it usable by anyone who has to decide whether a recording is what it claims to be.

## What it does

You upload a recording, or record one in the browser, and type what's claimed about it. Gridprint answers three questions:

- **When was it really recorded?** It matches the hum against a month of grid data and finds the start time, often to the second.
- **Was it edited?** If audio was cut out, it shows where, and measures how many seconds are missing.
- **Where could it have been made?** A 60 Hz hum means a 60 Hz grid, such as North America's, so a "London" recording humming at 60 Hz wasn't made in London.

An AI agent, NVIDIA Nemotron running on Nebius Token Factory, runs the investigation. You watch each step on screen as it happens. The result is a plain-English verdict: consistent with the claim, inconsistent, or inconclusive. It comes with the evidence, the odds of a coincidence (for example "1 in 10,300"), and a downloadable report. The report includes the file's SHA-256 fingerprint and a full evidence trail, so another analyst can check the work.

It is honest about its limits. If a recording has no hum, Gridprint says "inconclusive" rather than calling it fake, because a battery phone outdoors has no hum either.

## How we built it

**The physics (Python, NumPy, SciPy).** Gridprint reduces the audio to 1 kHz. Every second, it zooms the spectrum in on the 50, 100 and 150 Hz harmonics (chirp-z transform) and refines each peak by interpolation. It combines the harmonics, weighting each by its signal-to-noise ratio, because phones filter out 50 Hz and the higher harmonics often carry the signal.

**Matching with real statistics.** Gridprint slides the recording's hum along a month of NESO one-second data using overlap-add convolution. Then it borrows a tool from particle physics: the look-elsewhere correction. Searching a whole month gives chance thousands of tries, so the best match is judged against every other possible alignment. The result is an honest false-match probability, not a vague score.

**Two independent edit detectors.**
- **Phase jumps.** A cut makes the hum's phase jump. Gridprint tracks the phase with the analytic signal, and a cut only counts when several harmonics agree.
- **Timeline placement.** Each part of the recording is placed on the grid timeline separately. If the parts land at times that don't join up, a change-point scan finds the join and measures the missing seconds.

**The agent.** Nemotron runs through Nebius Token Factory, using OpenAI-compatible tool calling. It has five tools: analyse the recording, check which grid data is available, search the grid record, check for edits, and search the web with Tavily to find when an event named in a claim actually happened. It tests the claimed time first, widens the search if the claim fails, then writes the report. The model never touches the audio and can't make up numbers, because every figure comes from tested, deterministic code. If the model misbehaves, a fixed pipeline takes over, so there is always a report.

**The app.** A FastAPI server streams each step of the investigation to the page as it happens. The front end is plain HTML, CSS and JavaScript with no build step, in a quiet, Apple-inspired design with light and dark modes. Its one moment of drama is the recording's hum sliding onto the grid record and locking into place. Recording in the browser switches off noise suppression, because the hum is the evidence.

**Built to be safe in public.** The app is a Docker container running as an unprivileged user, hosted on a free Hugging Face Space. It enforces strict security headers, caps on upload size, duration and concurrent analyses, and plain-English error messages that never leak internals. A spending guard caps Nemotron calls per day and per visitor, so the public demo can't drain the hackathon credits. GitHub Actions runs 12 tests and a container build on every change.

## Challenges we ran into

- **Cuts that hide.** A cut that happens to land at nearly the same point in the hum's cycle leaves almost no phase jump. My first detector missed one in testing, so I added the second, timeline-based method. Each now catches what the other misses.
- **Honest uncertainty.** A short recording isn't always unique within a month of data. Getting the system to say so, instead of picking a match and sounding sure, took real statistics rather than prompt engineering.
- **Keeping the AI honest.** Language models like to fill gaps. I designed the tools so the model plans and explains while all measuring is done by code it can't override.
- **Messy real-world data.** The grid operator's files contain glitches, and their layout varies. The parser finds columns by their content and throws out physically impossible readings. Testing it caught a bug that would have broken on the newest version of pandas.
- **Browsers delete the evidence.** Default microphone settings filter out exactly the hum Gridprint needs, so the recorder switches those filters off.

## Accomplishments that we're proud of

- It recovers the exact second a recording started, searching a full month of grid data, even with the hum buried tens of decibels below the voice.
- On a test recording with 50 seconds secretly removed, it found the cut and measured the gap as 50 seconds.
- On [N] trials with real NESO data, it recovered the start time within 2 seconds [X/N] times, and caught [X/N] edited recordings with [X] false alarms.
- It's well calibrated: every result it reported with high confidence was correct, and every wrong answer came with a high false-match probability. When it doesn't know, it says so.
- The whole thing runs at no cost.

## What we learned

- The look-elsewhere effect isn't only a particle-physics idea. It's exactly the trap a naive forensic search falls into.
- AI agents work best when they plan and explain, and leave the measuring to deterministic tools.
- In forensics, "inconclusive" is a feature. Trust comes from being clear about what the evidence can and can't show.

## What's next for Gridprint

- **More grids:** reference data for continental Europe and North America.
- **Video:** LED and fluorescent lights flicker at twice the grid frequency, and phone cameras capture it, so the same method can work on silent video.
- **Live checks:** verifying recordings made today, using the near-live Elexon frequency feed, which is already wired in.
- **Real-world testing:** field recordings with known times, and work with newsrooms and fact-checkers.
- **A court-ready report** that sets out the method, data sources and uncertainty in a standard format.

*Note for judges: the demo runs on a free Hugging Face Space and may take about a minute to wake up the first time you open it.*

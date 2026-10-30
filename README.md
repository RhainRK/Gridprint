# Gridprint

**When was it really recorded?**

**Live demo:** https://YOUR-SERVICE.onrender.com · **Video:** https://youtu.be/YOUR-VIDEO

![Gridprint: the hum in a recording, laid on the power grid's own record](web/og.png)

Every plugged-in room hums faintly at the power grid's frequency. In Great Britain that's about 50 Hz, but never exactly: it drifts by a few hundredths of a hertz every second as demand rises and falls. The drift is identical across the whole country, and the grid operator, NESO, publishes it second by second. Microphones record the hum, even when you can't hear it.

Gridprint reads that hum and matches it against the grid's record. That answers three questions about a recording:

- **When was it really made?** Often to the second, found by searching a month of grid data.
- **Was it edited?** Gridprint finds cuts and measures how many seconds are missing.
- **Where could it have been made?** A 60 Hz hum means a 60 Hz grid, such as North America's, not Britain's.

An agent built on **NVIDIA Nemotron**, running on **Nebius Token Factory**, runs the investigation and writes a plain-English verdict: consistent with the claim, inconsistent, or inconclusive. When there's no hum to work with, it says so rather than guessing.

Built for the Nebius x NVIDIA Global AI Hackathon 2026.

## Try it

Open the live demo and pick one of the five example cases:

| Case | What it shows |
|---|---|
| A voice note | The hum dates the recording to the second, and the claim checks out |
| An alibi | The recording was made three days after the claimed date |
| A leaked meeting | An "unedited" recording with 50 seconds cut out |
| A whistleblower | A "London" recording with a 60 Hz American-grid hum |
| A CEO voicemail | No hum at all, so the verdict is inconclusive rather than fake |

The demo is on a free Render service. If nobody has used it recently, it takes about a minute to wake up.

## Run it yourself

You need Python 3.10 or newer. ffmpeg is optional: it's only needed for MP3, M4A, video files and in-browser recordings.

```bash
pip install -r requirements.txt
python scripts/build_reference.py --months 2025-01     # load a month of real NESO grid data
python scripts/make_examples.py                        # rebuild the five demo cases from it
python server.py                                       # then open http://127.0.0.1:7860
```

If the automatic download fails, download the month's CSV from the [NESO data portal](https://www.neso.energy/data-portal/system-frequency-data) and run `python scripts/build_reference.py --csv path/to/file.csv`.

To switch on the AI agent, set your keys before starting the server:

```bash
export NEBIUS_API_KEY=...      # Windows PowerShell: $env:NEBIUS_API_KEY="..."
export TAVILY_API_KEY=...      # optional: lets the agent look up when an event in a claim happened
```

Without a key, Gridprint still runs every measurement and writes its report from a built-in template.

## How it works

1. **Extract the hum** (`gridprint/enf.py`). The audio is reduced to 1 kHz. Every second, the spectrum is zoomed in on the 50, 100 and 150 Hz harmonics (chirp-z transform), and each peak is refined by interpolation. The harmonics are combined, weighted by signal-to-noise, because phones filter out 50 Hz and the higher harmonics often carry the signal.
2. **Match it to the grid** (`gridprint/match.py`). The hum slides along a month of one-second grid data, a day at a time, using overlap-add convolution. Each match is judged against every other alignment, then corrected for the look-elsewhere effect (searching a month gives chance thousands of tries). The result is a false-match probability, shown as plain odds.
3. **Check for edits** (`gridprint/enf.py`, `gridprint/forensics.py`). Two independent methods:
   - **Phase jumps.** A cut makes the hum's phase jump, and it only counts when several harmonics agree.
   - **Timeline placement.** Each part of the recording is placed on the grid timeline separately. Parts that don't join up reveal missing audio, and a change-point scan measures how much.
4. **Investigate and explain** (`gridprint/agent.py`). Nemotron calls these tools through Nebius Token Factory, using OpenAI-compatible tool calling, plus Tavily to look up when an event named in a claim happened. It tests the claimed time first, widens the search if the claim fails, and writes the report. The model never touches the audio, and every number comes from tested code. If the model fails, a fixed pipeline takes over.
5. **Show the evidence** (`server.py`, `web/`). A FastAPI server streams each step to the browser as it happens. The front end is plain HTML, CSS and JavaScript with light and dark modes. Every report records the file's SHA-256 fingerprint, the data used and the searches run, so another analyst can check the work.

## Deploying for free on Render

1. On [Render](https://render.com), click **New → Web Service** and connect this repository.
2. Choose **Docker** and the **Free** instance type. If the project files sit inside a folder in your repo, set **Root Directory** to that folder.
3. Add `NEBIUS_API_KEY` and `TAVILY_API_KEY` under **Environment Variables**.
4. Click **Deploy**.

Gridprint searches the grid data a day at a time, so it fits in the free tier's 512 MB of memory. The same container runs anywhere Docker runs:

```bash
docker build -t gridprint . && docker run -p 7860:7860 -e NEBIUS_API_KEY=... gridprint
```

### Settings

| Variable | Default | What it does |
|---|---|---|
| `NEBIUS_API_KEY` | none | Switches on the Nemotron agent |
| `TAVILY_API_KEY` | none | Lets the agent search the web for when events happened |
| `GRIDPRINT_MODEL` | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | Which Nemotron model to use |
| `GRIDPRINT_AI_DAILY_LIMIT` | `40` | Most AI-written reports per day, which protects your credits |
| `GRIDPRINT_AI_PER_VISITOR_PER_HOUR` | `6` | Most AI-written reports per visitor per hour |
| `GRIDPRINT_MAX_CONCURRENT` | `1` | How many analyses run at once (others wait their turn) |

## Testing

```bash
python -m pytest -q                         # 12 tests: physics, statistics, server, security, messy data
python scripts/evaluate.py --trials 40      # accuracy on random recordings hidden in the grid data
```

The five demo cases are checked when they're built, so the walkthrough shows each feature clearly. `evaluate.py` measures accuracy on random, unchecked trials.

## Limits, stated plainly

- **Gridprint needs mains hum.** Battery devices outdoors, strong noise suppression and heavy compression can remove it. Missing hum is not evidence of fakery.
- **It can only date recordings within the grid data it holds.** NESO publishes a month at a time, about a month late, so very recent recordings can't be dated yet.
- **Short recordings** (under about 2 minutes) may not be unique within a month of data. The odds of a coincidence say so honestly.
- **A skilled forger could add an artificial hum,** but matching it to the real grid record second by second is much harder.
- **The demo cases are synthesised:** grid frequency hidden in speech-band noise, which is the standard way ENF methods are tested. Unless real NESO data has been loaded with `build_reference.py`, the demo uses a synthetic test month and labels its results that way. Real field recordings vary more.
- **Gridprint is an investigative aid,** not an accredited forensic examination.

## Data and licence

- **Grid frequency:** NESO Data Portal, under the NESO Open Data Licence v1.0. *Supported by National Energy SO Open Data.* Gridprint is not endorsed by NESO.
- **Code:** MIT licence (see `LICENSE`).

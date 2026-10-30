# Demo video script (target 2:45, hard limit 3:00)

Record the screen with OBS or QuickTime, with your voice over it. Do one take per scene, then stitch them together. Have the app open with the examples loaded before you start. Before recording, run the cases once so the Nemotron calls are warm.

**0:00–0:15. Hook** (on screen: the Gridprint page, with "An alibi" picked)
> "This phone call is someone's alibi. He says it was recorded on Monday, at home. The UK's power grid says otherwise. Let me show you how."

**0:15–0:45. The physics** (on screen: click Analyse, scroll to the hum chart)
> "Everything plugged into the mains hums at fifty hertz. Except it's never exactly fifty: the grid frequency drifts a few hundredths of a hertz every second, the same everywhere in Britain, and the grid operator publishes it, second by second. Microphones pick up that hum. This brown line is the hum's frequency, pulled out of a recording where it's buried forty decibels under the voice."

**0:45–1:25. Case 1, the alibi** (on screen: the steps streaming in under "How Nemotron investigated", then the hum locking onto the grid record, then the verdict and statistics)
> "Nemotron, running on Nebius Token Factory, runs the investigation. It turns the claim into a time window and tests it first: Monday gives no match. So it widens the search to the whole month, and the hum lines up with the grid's record on Thursday, to the second. Blue is the grid, brown is the recording. The chance of that being a fluke, after correcting for how many places we looked, is about one in [number]: the look-elsewhere correction physicists use for particle searches."

**1:25–2:00. Case 2, the 'unedited' leak** (on screen: pick "A leaked meeting", then the yellow missing-audio band on the chart, then the "50 s" statistic)
> "This leak is claimed to be unedited. The hum's phase jumps here, and the two halves of the recording land at grid times that don't join up. Fifty seconds are missing, right there. Someone cut something out of this meeting."

**2:00–2:25. Cases 3 and 4, location and honesty** (on screen: pick "A whistleblower" and show its verdict, then "A CEO voicemail" and show its verdict)
> "This one claims to be from Canary Wharf, but the hum is at sixty hertz: it was recorded on an American-style grid. And this 'CEO voicemail' has no hum at all. Gridprint won't call it fake, because a battery phone outdoors has no hum either. It says inconclusive. That honesty is the point."

**2:25–2:50. How it's built** (on screen: the README diagram, or the evaluation output in a terminal)
> "The signal processing and statistics are deterministic Python, with tests. Nemotron plans and explains; Tavily dates events the claim mentions. On [N] trials with real grid data, it recovered the exact second [X] times out of [N], caught [X] of [N] cuts, and every answer it was confident about was right."

**2:50–3:00. Close** (on screen: the Gridprint title)
> "Deepfake detectors chase the last generator. Physics doesn't care which generator you used. This is Gridprint."

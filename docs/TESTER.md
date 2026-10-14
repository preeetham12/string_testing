# NUERiQ String Testing page

A browser page for testing string instruments: record a take, watch the notes
and the instrument being recognised **while playing**, check the result against
what you really played, and get a report at the end. It follows the
five-step flow of the NUERiQ keyboard test:

1. **Your details** (name, e-mail, instrument or "not sure", consent)
2. **Room check and recording** (3 s of room noise, then play; live string
   number, a pitch graph that scrolls left to right with the instrument's open
   strings as guide lines, and an instrument guess)
3. **Listen** (playback plus anything that makes the take unreliable)
4. **Check the form** (keys table: change / add / delete, each with a one-tap *why*)
5. **Done** (report; downloads as JSON, WAV or PDF). The report shows **how
   accurately the app heard the playing, note by note, as confirmed by the
   player** (green = right, amber = wrong note corrected, red = missed, grey =
   shown but not played) and **intonation by string**.

The analysis runs in the browser. When the test is submitted, the report and
the recording are **saved to the results dashboard** (if the page is served by
the StringCoach server; otherwise the page says so and offers the downloads).
The e-mail is kept on the device and never sent or stored.

## Results dashboard

Open `/tester/dashboard.html` on the StringCoach server. It lists every
submitted test (date, tester, instrument, notes, how many the app heard
correctly, intonation, recording), updates by itself every 15 seconds, opens
the full report with the recording, exports everything to CSV (Excel) and can
delete a test (testers may ask for their recordings to be deleted).

The dashboard is protected by a key: set `TESTER_DASHBOARD_KEY`, or the server
creates one on first start in `<data folder>/tester_dashboard_key.txt`
(`server_data/` by default). Testing pages can only *add* results; each new
result gets a one-time token that is the only way to attach its recording.

## Run it

```bash
uvicorn backend.api:app --port 8000
# testing page:      http://localhost:8000/tester/
# results dashboard: http://localhost:8000/tester/dashboard.html
```

(`python -m http.server 8080 --directory tester` still works for the testing
page alone, but then results can't be saved to the dashboard.)

The microphone needs `https` or `localhost`. To publish it, upload the
`tester/` folder to any static host (for example the bucket behind a
CloudFront link).

## What it recognises

| Instrument | How it is told apart |
|---|---|
| **Violin, viola, cello** | Bowed: soft attack (30-150 ms), level sustain, 4-8 Hz vibrato. Told apart by register: lowest and median note (nothing below G3 is a violin; nothing below C3 is a viola). |
| **Guitar** | Plucked: instant attack, dies away ~14 dB/s, high harmonics fade as the note rings, reaches below 100 Hz. |
| **Veena / sitar** | Plucked but rings long (~5 dB/s), the curved bridge keeps high harmonics alive, **glides (meend)** between notes while ringing. Sitar is judged as the same family. |
| **Ukulele** | Plucked, very short ring, nothing below C4. |
| **Mandolin** | Plucked, short ring, **tremolo** (loudness pulses 8-22 times a second). |

Violin/viola and ukulele/mandolin overlap in register and sound; when the
evidence cannot separate them the page says "Violin **or** viola" and the report
marks the pair as a close match instead of a miss.

Notes are shown by **string number** (string 1 = highest-pitched, the usual
way players number them), using standard tunings: violin and mandolin G-D-A-E,
viola and cello C-G-D-A, guitar E-A-D-G-B-E, ukulele G-C-E-A. A note is placed
on the highest open string at or below it (first position). Veena and sitar
have no single standard tuning, so their notes are shown by note name. "Cents" are measured against the nearest equal-tempered note after
removing the instrument's overall tuning offset; just-intonation swaras can
legitimately sit 10-20 cents away.

## What it rejects (so it does not score noise as music)

* **Talking**: a voice wobbles in pitch (~20 cents; a string under 8), is short,
  and is tracked less surely.
* **Knocks, claps, coughs**: the start of the sound is noise, not a tuned string
  (flatness 0.44-0.59 vs under 0.023 for every instrument). Such a hit also no
  longer cuts a ringing note in two.
* Too quiet, distorted, noisy room, hardly any playing, pauses of 3 s or more.

## How accurate is it?

Run the checks yourself:

```bash
node tests/tester/evaluate.js           # accuracy on 112 synthetic takes with known notes (~4.5 min)
node tests/tester/evaluate.js --quick   # 28 takes
npm i playwright-core && node tests/tester/browser.test.js    # real Chromium, fake microphone
```

Results on **synthetic** takes (7 instruments x 4 recording conditions x 4
seeds = 112 takes; clean, room echo, fan noise, quiet phone audio):

| | Result |
|---|---|
| Notes found (within 0.18 s and 50 cents) / reported notes that are real | 97% / 92% |
| Plucked vs bowed | 100% |
| Exact instrument | 86% |
| Instrument, counting violin/viola and ukulele/mandolin as one group | 100% |
| Exact, by instrument | violin, cello, guitar, veena, mandolin 100%; viola 50% (called violin); ukulele 50% (called mandolin) |
| Confidence when right / when wrong | 85% / 49% |
| Talking-only recordings flagged as talking | 5 of 5 (5 stray notes in 40 s of speech) |
| Talking in a pause during playing flagged | 10 of 10 |
| Knocks flagged / notes unaffected | 9 of 10 / 10 of 10 |
| False talking or knock alarms on clean instrument takes | 5 of 112 takes |
| Real Chromium with a fake microphone, all five steps, 4 recordings | 59 of 59 checks (violin, guitar, veena, talking + knock) |

Weakest spots: veena notes are found at 81% recall / 67% precision (a pluck over
a loudly ringing string leaves no clear loudness jump, and the changeover
produces extra fragments), and mandolin precision is 85% (tremolo repeats).

These are **not real instruments**. The synthetic sounds are built from the
same physical cues the classifier uses, so these numbers show the code does
what it was designed to do, not how it does on a real violin or veena. The
thresholds are starting values to be tuned on real recordings. That is what the
page is for: every submitted test pairs a real recording with what the player
says they played and which notes were right, and the report records both.

## Known limits

* Single notes only (melody lines). Chords and several strings at once are not
  analysed, and talking *while* playing is not detectable.
* Violin vs viola, ukulele vs mandolin: report as a pair.
* Instruments not in the list (double bass, harp, sarod, santoor, banjo...) will
  be labelled as the closest of the seven, usually with low confidence.
* A veena note that is re-plucked while the previous one is still ringing loudly
  can be missed or get a wrong first pitch; the check step exists to catch this.
* Needs about 4 clear notes before it will name an instrument, and 8-10 for a
  confident answer.

# StringCoach architecture (as built)

This describes the code in this repository. Product plans and screen designs
live in the separate solution-architecture doc and UX canvas.

## Components

```
browser (web/)                       server (backend/)                    engine (engine/)
--------------                       -----------------                    ----------------
record (AudioWorklet, raw PCM)  ->   chunked upload (checksummed)    ->   job queue -> worker:
live tuner + pitch lane (YIN)        auth, classes, lessons, attempts      quality -> clean -> pitch
review page, teacher studio     <-   results, comments, progress     <-    -> compare -> score -> feedback
```

- **Browser.** The microphone opens with voice processing off
  (`echoCancellation`, `noiseSuppression`, `autoGainControl` all false) and
  a 0.5 s warm-up. Pitch is detected on the audio thread, so the live tuner
  never stutters. A quick check (level, distortion, any playing) runs before
  upload so obviously bad takes never leave the device.
- **Server.** FastAPI. The API never waits for analysis: submitting queues a
  job and returns at once; the app polls `queued -> analysing -> done`.
  Jobs live in the database, so a job interrupted by a crash is re-queued on
  restart.
- **Engine.** Pure Python (librosa, numpy, scipy). The same pipeline runs for
  teachers' references and students' takes.

## Engine pipeline

| Step | Module | What it does | Key settings |
|---|---|---|---|
| Quality gate | `quality.py` | Clipping, level, dropouts, noise, playing detected, tuning, duration, same piece | Noise is measured while no note is playing, skipping the 0.4 s of room echo after each note; one tiny dropout warns, 3+ or >50 ms fails |
| Cleaning | `clean.py` | Hum detection + notches, steep high-pass below the instrument's lowest note, spectral gating, trim start/end silence only | Noise profile from moments within 6 dB of the quietest one, by total power |
| Note detection | `pitch.py` | pYIN (voiced or not, every 23 ms) + YIN (exact pitch, every 11.6 ms), must agree within 50 cents; notes split at bow changes (loudness dips) or pitch jumps of 60+ cents | Notes the tracker is under 30% sure of are dropped |
| Comparison | `compare.py` | Tuning offset first; open-end DTW on chroma; Needleman-Wunsch note matching (wrong 1, missed/extra 1 each, time penalty up to 0.6); tempo = median ratio of neighbouring note gaps | Restart detection ignores a lesson that repeats its own opening |
| Scoring | `score.py` | Sections start at 100; wrong/missed -15, extra -10, intonation up to -15, timing up to -10, dynamics up to -5, tone 0 | Intermediate: full marks within ±12 cents, zero at 35; pass mark 90 |
| Review | `feedback.py` | Rule-based sentences with timestamps; detail for the 2 weakest sections; unplayed sections in one line | No sentence without a measured finding |

## Student takes: pauses, repeated notes, timing

- **Pauses** (quiet for 1 s or more inside a take: thinking, turning a page)
  are cut out before checking (`pipeline.find_pauses`), mentioned once in
  the review without penalty, and every timestamp is mapped back to the
  student's original recording.
- **Sensitive note splitting** for students (3 dB loudness dips; teachers use
  a careful 6 dB). Pieces are then reconciled against the lesson: two pieces
  of ONE lesson note are joined (only if together they last about one note
  and have the same pitch); one note covering TWO repeated lesson notes is
  split (only if it really lasts as long as both).
- **Timing** is measured between heard note starts only. A "late" next to an
  equal "rushed" is one misplaced note start (room echo), so it is neither
  flagged nor scored.

## Talking is not playing (two safety layers)

Found in user testing: talking near the microphone was scored as playing
(speech has pitch). Every take now passes two layers before it is scored:

1. **Instrument check** (`quality.instrument_check`): a bowed note holds its
   pitch (steadiness >= 0.95; real violin 0.991-1.000, real speech 0.60-0.90)
   and notes fill most of the recording (>= 50%; violin 94%+, speech 0-23%).
   Measured on 40 real violin takes (4 conditions) and 24 real speech takes
   (6 speakers, clean and echoey): all violin accepted, no speech accepted.
   Also applied to teachers' references.
2. **Lesson check** (worker): if under a third of the lesson's notes are
   right (octave slips count as right), no score - "we couldn't recognise
   this lesson".

Rejected takes don't use up an attempt. When an older check also fails, the
instrument reason is shown, since it's the accurate one.

## Teacher references in real rooms

Echo can blend a teacher's repeated notes (A4, A4) into one, which shifts
every review after it by one note. The studio marks notes that may be two
(`pipeline.suggest_splits`: about twice a normal note's length, with a bow
change inside) and the teacher splits them with one click. Suggestions are
advisory: held notes look similar, so the teacher listens first.

## Speed

Analysis of a one-minute take: about 5 s for the whole server path on one
CPU core (was 10 s). The quality gate skips the fine pitch pass it doesn't
need; the teacher's features (chroma, per-note tone) are cached between
takes; time mappings are computed once and vectorised.

## Data model (`backend/db.py`)

`users`, `tokens`, `classes`, `enrollments`, `uploads` (chunked, resumable),
`recordings`, `assignments` (reference, level, pass mark, attempts, status
`processing -> ready -> published` or `reference_failed`, notes, sections),
`attempts` (status, counted, score, result, review), `comments` (text,
score override), `jobs`.

## Rules worth knowing

- **Privacy on every request.** Students see only their own attempts and
  published lessons of classes they're in; anything else returns 404 so IDs
  aren't revealed. Audio is served only to people allowed to hear it.
- **Fair attempts.** Takes rejected by the quality gate, or that fail to
  analyse, don't count. Section practice is unlimited. The attempt limit is
  enforced under a lock (8 simultaneous submits with 1 attempt left: exactly
  1 accepted).
- **Locked at publishing.** Teachers can fix notes (a misheard note or their
  own slip), delete stray notes and edit sections before publishing; after
  that both are locked so every student is marked against the same lesson.
  A deleted note's empty slot is not treated as a rest or a timing error.
- **Overrides are visible.** When a teacher overrides a score, both the
  teacher's and the automatic score are shown.

## Lessons from testing

Several real bugs were found only by testing with longer pieces, unseen
cases, a real browser or simultaneous requests. Each now has a test:

- Averaging decibels across frequencies made music look like silence, so the
  cleaner treated quiet music as noise and turned real notes down.
- A fixed "quietest 10%" noise estimate failed on long takes (quiet music
  counted as noise), both in cleaning and in the quality gate.
- Global DTW stretched a half-played piece over the whole lesson; open-end
  DTW fixed it.
- Browsers drop single 10 ms audio buffers; failing the whole take for that
  rejected every real take.
- Two simultaneous submits could bypass the attempt limit.
- First real-instrument test: the browser's pitch detector heard 0% of a bowed
  violin (bow noise) and blocked the take before upload; room echo made a
  quiet room look noisy on the server. Fixed with YIN's best-estimate fallback
  (preferring the first near-best dip, to avoid octave errors), letting the
  server make the final call, and skipping echo when measuring noise.
- The accuracy benchmark (60 realistic takes) showed most remaining errors
  came from the TEACHER's reference: echo blended repeated notes and shifted
  every review by one note. That led to the studio's Split tool.
- Real violin (Bach10) revealed: slivers split off the end of held notes in
  the teacher's recording (now not called "missed", and notes under 0.2 s
  are not judged for intonation or loudness); real pitch drifts ~20 cents
  within a held note (rejoin limit 30 cents); rejoined notes now use a
  length-weighted pitch. Clean control reviews: 44% -> 68% on identical takes.

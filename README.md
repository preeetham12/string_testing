# StringCoach

Practice platform for string instruments (violin, viola, cello; guitar
planned). A teacher records a piece; students practise against that
recording, and every take is compared with the teacher's note by note,
scored section by section, and explained in plain language. Teachers see
every take and how the whole class is doing.

> **Status: working prototype.** All parts are built and the test suite
> passes (156 checks, including 25 in a real browser), but every test uses
> synthetic audio and a simulated microphone. See [Status and next steps](#status-and-next-steps)
> before using it with real students.

## How it works

```
recording -> quality gate -> cleaning -> note detection -> comparison -> score -> written review
             engine/quality  engine/clean  engine/pitch    engine/compare engine/score engine/feedback
```

1. **Quality gate** rejects takes that can't be scored fairly (distortion,
   too quiet, too noisy, dropouts, no playing, wrong piece) with a fix the
   student understands. Rejected takes don't use up an attempt.
2. **Cleaning** removes mains hum (50/60 Hz, detected automatically),
   rumble and steady background noise.
3. **Note detection** uses two pitch trackers that must agree (pYIN decides
   *whether* a note sounds, YIN measures *exactly* which), and splits notes
   at bow changes, not at vibrato.
4. **Comparison** corrects for instrument tuning, aligns the two
   performances with open-end DTW (slower playing, pauses and stopping early
   are handled), then matches notes with Needleman-Wunsch: correct, wrong,
   missed or extra. False starts and octave mistakes are recognised.
5. **Scoring** works like a teacher's marking: each section starts at 100
   and mistakes cost points; tolerances depend on the level. A section passes
   at 90 (teachers can change this).
6. **Review** is rule-based: every sentence comes from a measured finding,
   with a timestamp in the student's own recording.

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## NUERiQ String Testing page

`tester/` is a separate browser page for testing string instruments: record a
take, see the notes and the instrument (violin, viola, cello, guitar, veena /
sitar, ukulele, mandolin) recognised **while playing**, check the result,
and get a report. It runs entirely in the browser. See
[docs/TESTER.md](docs/TESTER.md). Run it with
`python -m http.server 8080 --directory tester`, or open `/tester/` on the
StringCoach server.

## Demo in one command

```bash
pip install -r requirements.txt
python demo.py          # first run creates demo accounts, lessons and a sample take
```

Open <http://localhost:8000>. Teacher `priya@demo.in`, student
`arjun@demo.in`, password `violin-demo`. See [DEMO.md](DEMO.md) for the
walkthrough, honest accuracy numbers to quote, and fallbacks.

## Quick start

Needs Python 3.10+ (tested on 3.12).

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.api:app --port 8000
```

Open <http://localhost:8000> in Chrome, Edge or Firefox, register a teacher
and a student, and:

- **Teacher:** create a class, add the student by email, create a lesson
  (upload a WAV or record in the browser), check the notes and sections,
  publish.
- **Student:** open the lesson, listen (slower speeds keep the pitch),
  press Record (or Space), play, stop. The review appears in a few seconds.

Interactive API docs: <http://localhost:8000/docs>.

## Project layout

| Folder | What's in it |
|---|---|
| `engine/` | The analysis engine: `quality`, `clean`, `pitch`, `compare`, `score`, `feedback`, `pipeline`; `synth` makes test recordings |
| `backend/` | FastAPI server: `api` (endpoints), `db` (models), `auth`, `worker` (background analysis), `client` (chunked upload helper) |
| `web/` | The web app in plain JavaScript (no build step): `app.js`, `pitch-worklet.js` (live pitch on the audio thread), `index.html`, `style.css` |
| `demos/` | Runnable walkthroughs of each part that print results and draw charts (`python -m demos.part1_pitch` to `demos.part5_backend`) |
| `tests/` | Unseen-case suites, backend tests (pytest), browser tests (Playwright) |
| `docs/` | Architecture notes |

## Testing

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium          # for the browser tests
npm install --prefix /tmp/axe axe-core@4        # accessibility checker
python run_all_tests.py                         # everything, about 7 minutes
```

| Suite | Checks |
|---|---|
| Note and pitch detection | 7 |
| Quality gate: designed + unseen cases | 11 + 16 |
| Comparison: designed + unseen students | 20 + 15 |
| Scoring, feedback, practice loop + unseen cases | 19 + 13 |
| Backend API | 30 |
| Student web app in a real browser | 14 |
| Teacher studio in a real browser | 11 |

### Accuracy benchmark

```bash
python -m tests.benchmark            # 60 realistic takes, about 4 minutes
python -m tests.benchmark --checked  # the teacher has used the studio's Split tool
```

60 student takes of two pieces in five recording conditions (clean, home
room, phone across the room, fan noise, phone audio format), each with 0-4
planted mistakes (wrong, flat, sharp, missed, extra, hesitation, long pause).
It reports how many planted mistakes were found, false alarms, how often the
whole review is exactly right, and speed. Latest results (simulated audio):

| | Without teacher fixes | Teacher used the Split tool |
|---|---|---|
| Reviews exactly right (all 60 takes) | 85% | 93% |
| Clean / home room / phone format | 100% / 75% / 100% | 100% / 100% / 100% |
| Fan noise / phone across the room | 92% / 58% | 92% / 75% |
| Long pauses handled without penalty | 80% | 100% |
| Speed | 3.6 s per minute of music | 3.7 s per minute of music |

Split suggestions found both notes really blended by echo, but only 2 of 5
suggestions were right (held notes look similar), so teachers should listen
before splitting. These results are on simulated audio; real recordings are
the next test.

### Real violin benchmark (Bach10)

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/flippy-fyp/Bach10_v1.1.git Bach10
cd Bach10 && git sparse-checkout set --no-cone '*-violin.wav' '*-GTNotes.mat' && cd ..
python -m tests.real_violin_benchmark Bach10   # 150 takes, about 50 minutes; resumable
python -m tests.latency_trend Bach10           # latency for 15-120 s takes
python -m demos.real_violin_charts             # output/real_violin/real_violin_trends.png
```

10 real solo violin recordings with annotated notes. Student takes are the
real recordings with mistakes planted at annotated notes, in five recording
conditions; 50 control takes have no mistakes. Results:

| | Result |
|---|---|
| Real violinist's notes found / reported notes that are real | 95.0% / 96.6% (note starts within 9-22 ms) |
| Control takes with a clean review | 70% (0.42 false alarms per take) |
| Reviews exactly right (all planted problems caught, no false alarms) | 41% strict, 46% counting related labels |
| By condition (exactly right) | phone format 60%, as recorded 60%, home room 37%, fan 33%, far mic 13% |
| Mistakes caught | extra 96%, flat 93%, sharp 90%, pause 86%, wrong 75%, missed 73%, hesitation 69% |
| Latency | about 6.5 s per minute of music on one CPU core (1.6 s for 15 s, 14.1 s for 2 min) |

After the demo-release fixes (fake short "notes" from bow noise ignored;
fast notes blurred by echo no longer called missed), on the three realistic
conditions (as recorded, home room, fan):

| | Pieces 01-04 (used for tuning) | Pieces 05-10 (held out) |
|---|---|---|
| Reviews exactly right | 69% | 69% |
| Every problem caught, no false alarms | 78% | 70% |
| Clean review when nothing was wrong | 12/12 | 16/18 |
| Caught (held out) | | missed 100%, extra 100%, late 94%, pause 94%, wrong 86%, flat/sharp 100% |

Real violin is clearly harder than the synthetic benchmark. Room echo and
distant microphones are the weak spots, so students should record close to
the instrument in a quiet room. Limitation: every "student" is the same
real performer with planted mistakes; two different players still need
real student recordings.

The browser tests feed WAV files into Chromium as a fake microphone, so
recording, live pitch, upload, analysis and the review are all exercised for
real. Run one suite with, for example, `python -m pytest tests/test_backend.py -v`.

## Status and next steps

**Not yet validated with real instruments.** The most important next step is
recording real teachers and students (violin, cello, guitar) and having a
teacher grade the same takes, to tune the scoring to human judgement.

Prototype pieces to replace for production:

| Prototype | Production |
|---|---|
| SQLite | PostgreSQL (set `DATABASE_URL`) |
| Local `server_data/` folder | S3 (or compatible) object storage |
| Database job queue + worker threads | Celery + Redis |
| In-process attempt-limit lock | PostgreSQL row lock (`SELECT ... FOR UPDATE`) |
| Tokens that never expire | Expiring tokens, password reset, school sign-in |

Also still to do: saving recordings on the device before upload (offline
practice), live result updates instead of polling, polyphonic guitar
(chords), encrypted storage and data-deletion rules, parental consent for
under-18 students, and a compliance review (for example India's DPDP Act).

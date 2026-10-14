# StringCoach: first demo

## The night before (15 minutes)

1. Install (Python 3.10+):
   ```bash
   python -m venv venv
   source venv/bin/activate          # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Start once, so the demo data is created and checked:
   ```bash
   python demo.py --reset
   ```
   Wait for "StringCoach demo is running", open <http://localhost:8000>, log
   in as the teacher, and check that "Twinkle, 4 phrases" is **Published**.
3. Rehearse the walkthrough below **with the microphone you will use**, in
   the room you will use. Run the mic check there.
4. Use **Chrome**. Allow the microphone when asked. Use `localhost`, not an IP
   address (browsers only allow the microphone on localhost or https).

## On the day

```bash
python demo.py
```

| Who | Email | Password |
|---|---|---|
| Teacher | priya@demo.in | violin-demo |
| Student | arjun@demo.in | violin-demo |

Teacher in a normal Chrome window, student in an **Incognito** window.

## Walkthrough (about 10 minutes)

1. **The problem** (1 min). Students practise alone between lessons and
   don't know what they're getting wrong; teachers can't listen to every
   practice session.
2. **Teacher: class progress** (2 min). Open *Twinkle, 4 phrases*. Show the
   class heatmap (section 3 is the hard one), the hardest notes, and Meera's
   row. Open **Takes** for Meera: play her recording, show the review with
   timestamps, add a comment and a score override.
3. **Teacher: the studio** (2 min). Open *G major scale* (not yet
   published). Play it: the notes light up in sync. Select a note: show how a
   misheard note is fixed (semitone up/down) or split. Mention that notes and
   sections lock when published, so every student is marked the same way.
4. **Student: practise** (4 min). In Incognito, log in as Arjun, open
   *Twinkle*. Listen to the teacher at half speed (pitch stays the same).
   Press **Check microphone**. Record (Space to start and stop). Show the live
   tuner and the pitch lane following the teacher's blue notes. Stop, and the
   review appears in a few seconds: score, sections, each mistake with a
   timestamp, a practice tip, and **Practise section N**.
5. **Optional: it knows talking isn't playing** (30 s). Press Record and
   just talk for a few seconds. It replies "This take wasn't counted ... this
   doesn't sound like your instrument being played", gives no score, and the
   attempt isn't used up. (Tested against real speech from 6 speakers: none
   was accepted; all 40 real violin takes were.)
6. **Close** (1 min). Back to the teacher: Arjun's take appears in the
   dashboard.

Tip: if nobody can play violin live, play `demo_data/audio/meera_take1.wav`
from a phone held near the laptop while the student records.

## What to say about accuracy (honest numbers)

- Tested on **10 real violin recordings** (the Bach10 research dataset):
  it hears **95%** of a real violinist's notes, with note starts within
  about 10-20 ms.
- Mistakes planted into those real recordings, on pieces it was **never
  tuned on**, recorded as-is, in an echoey room, or with a fan running:
  - it catches **every** skipped and extra note, **94%** of hesitations and
    pauses, **86%** of wrong notes, and every clearly out-of-tune note
  - **7 in 10 reviews are exactly right** (every mistake, nothing invented)
  - when nothing was wrong, **16 of 18** reviews were completely clean
- It **refuses to score talking or other sounds**, and won't score a take
  that doesn't match the lesson ("we couldn't recognise this lesson").
- It works best **close to the microphone in a quiet room**; a phone far
  across an echoey room is noticeably less accurate.
- A review is ready in about **7 seconds per minute of music**.
- **Not yet tested with real students**: every test so far uses real or
  realistic recordings with planted mistakes. The next step is a pilot with
  a real teacher and students, comparing the app's marks with the teacher's.

## If something goes wrong

| Problem | Fix |
|---|---|
| Microphone blocked | Click the lock icon in the address bar, set Microphone to Allow, reload |
| "Your recording is too quiet" | Move closer, or select the right microphone in system settings |
| "Address already in use" | `python demo.py --port 8001`, then open localhost:8001 |
| Demo data looks wrong | Stop, then `python demo.py --reset` |
| Live recording fails entirely | Show Meera's existing take and review instead (step 2) |

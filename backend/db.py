"""
Part 5 - Database.

SQLAlchemy models. The prototype uses SQLite (a single file, nothing to
install); production switches to PostgreSQL by changing DATABASE_URL only.
"""
import datetime as dt
import os

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = os.environ.get("STRINGCOACH_DATA", "server_data")
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR}/stringcoach.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}
                       if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()
now = lambda: dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # "teacher" or "student"


class Token(Base):
    __tablename__ = "tokens"
    token = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created = Column(DateTime, default=now)


class Class(Base):
    __tablename__ = "classes"
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    instrument = Column(String, nullable=False)  # violin / viola / cello / guitar


class Enrollment(Base):
    __tablename__ = "enrollments"
    id = Column(Integer, primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    __table_args__ = (UniqueConstraint("class_id", "student_id"),)


class Upload(Base):
    """A chunked upload in progress (like an S3 multipart upload)."""
    __tablename__ = "uploads"
    id = Column(String, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    total_chunks = Column(Integer, nullable=False)
    total_bytes = Column(Integer, nullable=False)
    sha256 = Column(String, nullable=False)  # checksum of the whole file
    received = Column(JSON, default=list)    # chunk numbers received so far
    status = Column(String, default="uploading")  # uploading / complete
    recording_id = Column(Integer, ForeignKey("recordings.id"))
    created = Column(DateTime, default=now)


class Recording(Base):
    __tablename__ = "recordings"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    path = Column(String, nullable=False)
    bytes = Column(Integer)
    created = Column(DateTime, default=now)


class Assignment(Base):
    __tablename__ = "assignments"
    id = Column(Integer, primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    title = Column(String, nullable=False)
    reference_id = Column(Integer, ForeignKey("recordings.id"), nullable=False)
    level = Column(String, default="intermediate")
    pass_mark = Column(Integer, default=90)
    max_attempts = Column(Integer, default=10)
    # processing -> ready (teacher reviews) -> published (students can see)
    # or reference_failed (teacher must re-record)
    status = Column(String, default="processing")
    sections = Column(JSON)          # list of {id, first, last, t_start, t_end}
    notes = Column(JSON)             # teacher's detected notes
    analysis_path = Column(String)   # cleaned reference audio, for comparisons
    ref_offset = Column(Float, default=0.0)  # silence trimmed from the start of the
                                             # reference; note times + this = raw time
    qc_report = Column(JSON)
    created = Column(DateTime, default=now)


class Attempt(Base):
    __tablename__ = "attempts"
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False)
    section_id = Column(Integer)     # None = full piece, else a practice section
    # queued -> analysing -> done | qc_failed | error
    status = Column(String, default="queued")
    counted = Column(Boolean, default=True)  # qc_failed attempts don't count
    score = Column(Integer)
    result = Column(JSON)            # sections, components, flags
    feedback = Column(Text)
    qc_report = Column(JSON)
    seconds = Column(Float)          # analysis time
    created = Column(DateTime, default=now)


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    attempt_id = Column(Integer, ForeignKey("attempts.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    score_override = Column(Integer)
    created = Column(DateTime, default=now)


class TesterResult(Base):
    """A finished test from the NUERiQ String Testing page (tester/)."""
    __tablename__ = "tester_results"
    id = Column(String, primary_key=True)          # random, unguessable
    created = Column(DateTime, default=lambda: dt.datetime.now(dt.timezone.utc))
    name = Column(String)                          # the tester's name (no e-mail is stored)
    instrument = Column(String)                    # what they said they played
    detected = Column(String)                      # what the app detected
    notes = Column(Integer)
    verified_pct = Column(Float)                   # notes the app got right, confirmed by the player
    within15_pct = Column(Float)                   # notes within +-15 cents
    seconds = Column(Float)
    quality = Column(String)                       # "good" / "could be better"
    report = Column(JSON)
    audio_path = Column(String)
    upload_token = Column(String)                  # one-time: attaches the recording
    audio_received = Column(Boolean, default=False)


class Job(Base):
    """Background work queue, stored in the database so nothing is lost if the
    server restarts. (Production: Celery + Redis, same job types.)"""
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False)       # process_reference / process_attempt
    target_id = Column(Integer, nullable=False)
    status = Column(String, default="queued")   # queued / running / done / failed
    error = Column(Text)
    created = Column(DateTime, default=now)


def init_db():
    Base.metadata.create_all(engine)

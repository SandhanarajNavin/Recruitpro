"""ORM tables.

Two families, mirroring architecture doc §2. Candidate-side tables are the persistent
repository and grow by version; job-side tables are per-role, and a screening is one
immutable run. The only edge between the families is
``ScreeningResult.candidate_id`` — a result points at a candidate and never mutates one.
"""

from app.db.models.application import (
    ACTIVE_STAGES,
    PIPELINE_ORDER,
    Application,
    ApplicationEvent,
    ApplicationStage,
    Interview,
    InterviewOutcome,
)
from app.db.models.audit import AuditAction, AuditEvent
from app.db.models.base import TimestampMixin, uuid_pk
from app.db.models.candidate import Candidate, CandidateProfile, CandidateStatus
from app.db.models.chat import ChatConversation, ChatMessage, ChatRole
from app.db.models.embedding import Embedding, EmbeddingOwner
from app.db.models.evaluation_cache import EvaluationCache
from app.db.models.exclusion import JobCandidateExclusion
from app.db.models.job import Job, JobRequirement
from app.db.models.resume import Resume, ResumeStatus
from app.db.models.resume_chunk import ResumeChunk
from app.db.models.screening import Explanation, Screening, ScreeningResult
from app.db.models.search import (
    SearchResult,
    SearchSession,
    SearchSessionStatus,
)
from app.db.models.taxonomy import (
    CandidateIndustry,
    CandidateSkill,
    Industry,
    JobSkill,
    Proficiency,
    Skill,
)
from app.db.models.user import User

__all__ = [
    "ACTIVE_STAGES",
    "PIPELINE_ORDER",
    "Application",
    "JobCandidateExclusion",
    "ResumeChunk",
    "ApplicationEvent",
    "ApplicationStage",
    "AuditAction",
    "AuditEvent",
    "ChatConversation",
    "ChatMessage",
    "ChatRole",
    "Candidate",
    "CandidateIndustry",
    "CandidateSkill",
    "CandidateProfile",
    "CandidateStatus",
    "Embedding",
    "EvaluationCache",
    "EmbeddingOwner",
    "Explanation",
    "Industry",
    "Interview",
    "InterviewOutcome",
    "Job",
    "JobRequirement",
    "JobSkill",
    "Proficiency",
    "Resume",
    "ResumeStatus",
    "Screening",
    "ScreeningResult",
    "SearchResult",
    "SearchSession",
    "SearchSessionStatus",
    "Skill",
    "TimestampMixin",
    "User",
    "uuid_pk",
]

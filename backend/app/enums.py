from enum import Enum


class UserRole(str, Enum):
    INSTRUCTOR = "INSTRUCTOR"
    TA = "TA"
    STUDENT = "STUDENT"
    ADMIN = "ADMIN"


class ProgrammingLanguage(str, Enum):
    CPP = "CPP"
    PYTHON = "PYTHON"
    JAVA = "JAVA"
    C = "C"
    JAVASCRIPT = "JAVASCRIPT"


class AssignmentStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


class EvaluationType(str, Enum):
    TEST = "TEST"
    LLM = "LLM"
    HYBRID = "HYBRID"


class TestVisibility(str, Enum):
    PUBLIC = "PUBLIC"
    HIDDEN = "HIDDEN"


class TestSource(str, Enum):
    INSTRUCTOR = "INSTRUCTOR"
    LLM = "LLM"


class TestCategory(str, Enum):
    NORMAL = "NORMAL"
    EDGE = "EDGE"
    BOUNDARY = "BOUNDARY"
    STRESS = "STRESS"
    ADVERSARIAL = "ADVERSARIAL"


class TestStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
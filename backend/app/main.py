from fastapi import FastAPI

from app.routers.assignments import router as assignment_router
from app.routers.rubrics import router as rubric_router
from app.routers.test_cases import router as test_case_router
from app.routers.reference_solutions import (
    router as reference_solution_router
)
from app.routers.submissions import (
    router as submission_router
)


app = FastAPI(
    title="Reliable LLM Assignment Grading System",
    version="1.0.0"
)


app.include_router(assignment_router)
app.include_router(rubric_router)
app.include_router(test_case_router)
app.include_router(reference_solution_router)
app.include_router(submission_router)


@app.get("/")
def root():
    return {
        "message": "Grading System API is running"
    }
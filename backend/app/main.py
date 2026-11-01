from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.grading.errors import GradingError

from app.routers.assignments import router as assignment_router
from app.routers.rubrics import router as rubric_router
from app.routers.test_cases import router as test_case_router
from app.routers.reference_solutions import (
    router as reference_solution_router
)
from app.routers.submissions import (
    router as submission_router
)
from app.routers.objective_grading import router as objective_grading_router
from app.routers.auth import router as auth_router
from app.routers.assignment_memberships import router as assignment_membership_router


app = FastAPI(
    title="Reliable LLM Assignment Grading System",
    version="1.0.0"
)


@app.exception_handler(GradingError)
async def objective_grading_error(_request: Request, exc: GradingError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def safe_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default errors include raw request inputs. Credentials, source,
    # and private test contents must not be echoed in validation responses.
    details = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
               for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": details})


@app.exception_handler(SQLAlchemyError)
async def safe_database_error(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
    # Do not log exception text/SQL parameters: legacy services can be writing
    # credentials or private test evidence when a database operation fails.
    return JSONResponse(status_code=503, content={"detail": "Database operation is unavailable; retry later"})


app.include_router(assignment_router)
app.include_router(rubric_router)
app.include_router(test_case_router)
app.include_router(reference_solution_router)
app.include_router(submission_router)
app.include_router(objective_grading_router)
app.include_router(auth_router)
app.include_router(assignment_membership_router)


@app.get("/")
def root():
    return {
        "message": "Grading System API is running"
    }

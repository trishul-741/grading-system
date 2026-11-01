"""Exercise existing assignment/submission APIs without Docker or PostgreSQL.

These tests preserve current demo-user behavior; they do not certify it as
authentication. SQLite covers HTTP/service regressions, not PostgreSQL migrations.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.enums import (
    AssignmentStatus,
    EvaluationType,
    ProgrammingLanguage,
    TestStatus as CaseStatus,
)
from app.models import (
    Assignment,
    Rubric,
    Submission,
    TestCase as AssignmentTestCase,
)


STUDENT_ID = UUID("00000000-0000-0000-0000-000000000002")
OTHER_STUDENT_ID = UUID("00000000-0000-0000-0000-000000000003")


def create_assignment(client: TestClient, **overrides: object) -> dict:
    payload = {
        "title": "Add two numbers",
        "description": "Read two integers and print their sum.",
        "language": "CPP",
        "constraints": "Integers in [-100, 100]",
        "input_format": "Two integers",
        "output_format": "One integer",
    }
    payload.update(overrides)
    response = client.post("/assignments/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def publish_assignment(client: TestClient, db_session: Session) -> dict:
    created = create_assignment(client)
    assignment_id = UUID(created["id"])
    db_session.add_all(
        [
            Rubric(
                assignment_id=assignment_id,
                criterion="Correctness",
                max_marks=Decimal("10"),
                weight=Decimal("100"),
                evaluation_type=EvaluationType.TEST,
            ),
            AssignmentTestCase(
                assignment_id=assignment_id,
                input="1 2",
                expected_output="3",
                status=CaseStatus.APPROVED,
            ),
        ]
    )
    db_session.commit()
    response = client.put(
        f"/assignments/{created['id']}", json={"status": "PUBLISHED"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_assignment_crud(client: TestClient) -> None:
    created = create_assignment(client)
    assignment_url = f"/assignments/{created['id']}"
    assert created["status"] == "DRAFT"
    assert created["created_by"] == "00000000-0000-0000-0000-000000000001"
    assert created["created_at"] and created["updated_at"]
    assert client.get(assignment_url).json() == created
    assert [item["id"] for item in client.get("/assignments/").json()] == [created["id"]]

    updated = client.put(
        assignment_url,
        json={"title": "Sum integers", "constraints": None, "language": "PYTHON"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Sum integers"
    assert updated.json()["constraints"] is None
    assert updated.json()["language"] == "PYTHON"
    assert updated.json()["description"] == created["description"]

    deleted = client.delete(assignment_url)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(assignment_url).status_code == 404
    assert client.get("/assignments/").json() == []


def test_existing_routes_remain_available_without_public_execution(client: TestClient) -> None:
    assert client.get("/").json() == {"message": "Grading System API is running"}
    assignment = create_assignment(client)
    prefix = f"/assignments/{assignment['id']}"
    assert client.get(prefix + "/rubrics/").json() == []
    assert client.get(prefix + "/tests/").json() == []
    missing_reference = client.get(prefix + "/reference-solution/")
    assert missing_reference.status_code == 404
    assert missing_reference.json()["detail"] == "Reference solution not found"
    assert client.post(
        "/execution/cpp", json={"source_code": "int main() {}", "input_data": ""}
    ).status_code == 404
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/execution") for path in paths)


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_unknown_assignment(client: TestClient, method: str) -> None:
    kwargs = {"json": {"title": "Missing"}} if method == "put" else {}
    response = getattr(client, method)(f"/assignments/{uuid4()}", **kwargs)
    assert response.status_code == 404
    assert response.json()["detail"] == "Assignment not found"


@pytest.mark.parametrize(
    "overrides", [{"title": ""}, {"description": ""}, {"language": "RUST"}]
)
def test_assignment_validation(client: TestClient, overrides: dict) -> None:
    response = client.post(
        "/assignments/",
        json={
            "title": "Example",
            "description": "Example description",
            "language": "CPP",
            **overrides,
        },
    )
    assert response.status_code == 422
    assert client.get("/assignments/").json() == []


def test_publish_prerequisites(client: TestClient, db_session: Session) -> None:
    created = create_assignment(client)
    url = f"/assignments/{created['id']}"
    assignment_id = UUID(created["id"])

    response = client.put(url, json={"status": "PUBLISHED"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Assignment must have at least one rubric"

    rubric = Rubric(
        assignment_id=assignment_id,
        criterion="Correctness",
        max_marks=Decimal("10"),
        weight=Decimal("50"),
        evaluation_type=EvaluationType.TEST,
    )
    db_session.add(rubric)
    db_session.commit()
    db_session.expire_all()
    response = client.put(url, json={"status": "PUBLISHED"})
    assert response.status_code == 400
    assert "Rubric weights must total 100%" in response.json()["detail"]

    rubric.weight = Decimal("100")
    test_case = AssignmentTestCase(
        assignment_id=assignment_id,
        input="1 2",
        expected_output="3",
        status=CaseStatus.DRAFT,
    )
    db_session.add(test_case)
    db_session.commit()
    db_session.expire_all()
    response = client.put(url, json={"status": "PUBLISHED"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Assignment must have at least one approved test case"
    assert client.get(url).json()["status"] == "DRAFT"

    test_case.status = CaseStatus.APPROVED
    db_session.commit()
    db_session.expire_all()
    response = client.put(url, json={"status": "PUBLISHED"})
    assert response.status_code == 200
    assert response.json()["status"] == "PUBLISHED"


def test_submission_create_read_list_and_numbering(
    client: TestClient, db_session: Session, token_for
) -> None:
    assignment = publish_assignment(client, db_session)
    url = f"/assignments/{assignment['id']}/submissions/"
    headers = {"Authorization": "Bearer " + token_for(STUDENT_ID)}
    assert client.get(url, headers=headers).json() == []
    submissions = []
    for number in (1, 2):
        response = client.post(
            url, json={"source_code": f"// attempt {number}", "language": "CPP"}, headers=headers
        )
        assert response.status_code == 201, response.text
        submission = response.json()
        assert submission["submission_number"] == number
        assert submission["status"] == "SUBMITTED"
        assert submission["assignment_id"] == assignment["id"]
        assert submission["student_id"] == str(STUDENT_ID)
        assert submission["submitted_at"]
        assert client.get(url + submission["id"], headers=headers).json() == submission
        submissions.append(submission)

    other = Submission(
        assignment_id=UUID(assignment["id"]),
        student_id=OTHER_STUDENT_ID,
        submission_number=1,
        source_code="// private",
        language=ProgrammingLanguage.CPP,
    )
    db_session.add(other)
    db_session.commit()
    listed = client.get(url, headers=headers).json()
    assert {item["id"] for item in listed} == {item["id"] for item in submissions}
    assert client.get(url + str(other.id), headers=headers).status_code == 404


@pytest.mark.parametrize("assignment_status", ["DRAFT", "CLOSED", "ARCHIVED"])
def test_submission_rejects_non_published_assignment(
    client: TestClient, db_session: Session, assignment_status: str, token_for
) -> None:
    created = create_assignment(client)
    assignment = db_session.get(Assignment, UUID(created["id"]))
    assignment.status = AssignmentStatus(assignment_status)
    db_session.commit()
    response = client.post(
        f"/assignments/{created['id']}/submissions/",
        json={"source_code": "int main() {}", "language": "CPP"},
        headers={"Authorization": "Bearer " + token_for(STUDENT_ID)},
    )
    assert response.status_code == (400 if assignment_status == "CLOSED" else 404)
    assert db_session.query(Submission).count() == 0


def test_submission_rejects_language_mismatch(
    client: TestClient, db_session: Session, token_for
) -> None:
    assignment = publish_assignment(client, db_session)
    response = client.post(
        f"/assignments/{assignment['id']}/submissions/",
        json={"source_code": "print(3)", "language": "PYTHON"},
        headers={"Authorization": "Bearer " + token_for(STUDENT_ID)},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Submission language does not match assignment language"
    assert db_session.query(Submission).count() == 0


@pytest.mark.parametrize("method", ["get", "post"])
def test_submission_unknown_assignment(client: TestClient, method: str, token_for) -> None:
    kwargs = (
        {"json": {"source_code": "int main() {}", "language": "CPP"}}
        if method == "post"
        else {}
    )
    response = getattr(client, method)(
        f"/assignments/{uuid4()}/submissions/", headers={"Authorization": "Bearer " + token_for(STUDENT_ID)}, **kwargs
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Assignment not found"


def test_submission_missing_and_wrong_assignment(
    client: TestClient, db_session: Session, token_for
) -> None:
    assignment = publish_assignment(client, db_session)
    url = f"/assignments/{assignment['id']}/submissions/"
    response = client.get(url + str(uuid4()))
    assert response.status_code == 404
    created = client.post(
        url, json={"source_code": "int main() {}", "language": "CPP"}, headers={"Authorization": "Bearer " + token_for(STUDENT_ID)}
    ).json()
    other_assignment = create_assignment(client, title="Other assignment")
    response = client.get(
        f"/assignments/{other_assignment['id']}/submissions/{created['id']}"
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"source_code": "", "language": "CPP"},
        {"source_code": "int main() {}", "language": "RUST"},
    ],
)
def test_submission_payload_validation(client: TestClient, payload: dict, token_for) -> None:
    created = create_assignment(client)
    response = client.post(
        f"/assignments/{created['id']}/submissions/", json=payload, headers={"Authorization": "Bearer " + token_for(STUDENT_ID)}
    )
    assert response.status_code == 422


def test_assignment_delete_cascades_related_records(
    client: TestClient, db_session: Session, token_for
) -> None:
    assignment = publish_assignment(client, db_session)
    response = client.post(
        f"/assignments/{assignment['id']}/submissions/",
        json={"source_code": "int main() {}", "language": "CPP"},
        headers={"Authorization": "Bearer " + token_for(STUDENT_ID)},
    )
    assert response.status_code == 201
    assert client.delete(f"/assignments/{assignment['id']}").status_code == 204
    assert db_session.query(Assignment).count() == 0
    assert db_session.query(Rubric).count() == 0
    assert db_session.query(AssignmentTestCase).count() == 0
    assert db_session.query(Submission).count() == 0

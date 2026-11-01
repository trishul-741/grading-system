"""HTTP and direct-service ownership checks across every protected resource."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.enums import AssignmentStatus, EvaluationType, ProgrammingLanguage, TestStatus as CaseStatus, TestVisibility as Visibility, UserRole
from app.grading.config import ObjectiveGradingSettings, get_objective_grading_settings
from app.grading.errors import GradingError
from app.models import Assignment, AssignmentTAMembership, ReferenceSolution, Rubric, Submission, User


def headers(token_for, role: str) -> dict:
    return {"Authorization": "Bearer " + token_for(role)}


@pytest.fixture
def protected_records(db_session, objective_records, users):
    owner = objective_records.assignment
    public = objective_records.add_test("public-input", "public-output")
    hidden = objective_records.add_test("hidden-input-SENSITIVE", "hidden-output-SENSITIVE", visibility=Visibility.HIDDEN)
    private_cases = [objective_records.add_test("hidden-" + status.value, "private", visibility=Visibility.HIDDEN, status=status) for status in (CaseStatus.DRAFT, CaseStatus.VALIDATED, CaseStatus.REJECTED)]
    public_draft = objective_records.add_test("unapproved-public-SENSITIVE", "unapproved-output", status=CaseStatus.DRAFT)
    rubric = Rubric(assignment_id=owner.id, criterion="Correctness", max_marks=10, weight=100, evaluation_type=EvaluationType.TEST)
    solution = ReferenceSolution(assignment_id=owner.id, language=ProgrammingLanguage.CPP, source_code="reference-private-source-SENSITIVE")
    other_assignment = Assignment(created_by=users["other_instructor"].id, title="Other instructor draft", description="Private assignment", language=ProgrammingLanguage.CPP, status=AssignmentStatus.DRAFT)
    membership = AssignmentTAMembership(assignment_id=owner.id, ta_id=users["ta"].id, granted_by=users["instructor"].id)
    other_submission = Submission(assignment_id=owner.id, student_id=users["other_student"].id, submission_number=1, source_code="other-student-private-source-SENSITIVE", language=ProgrammingLanguage.CPP)
    db_session.add_all([rubric, solution, other_assignment, membership, other_submission])
    db_session.commit()
    return SimpleNamespace(assignment=owner, public=public, hidden=hidden, private_cases=private_cases, public_draft=public_draft, rubric=rubric, solution=solution, other_assignment=other_assignment, submission=objective_records.submission, other_submission=other_submission)


@pytest.mark.parametrize("path", ["/assignments/", "/assignments/{a}", "/assignments/{a}/rubrics/", "/assignments/{a}/tests/", "/assignments/{a}/reference-solution/", "/assignments/{a}/submissions/", "/assignments/{a}/tas"])
def test_resources_require_real_authentication(raw_client, protected_records, path: str) -> None:
    assert raw_client.get(path.format(a=protected_records.assignment.id)).status_code == 401


@pytest.mark.parametrize("role,expected", [("instructor", 200), ("admin", 200), ("ta", 200), ("student", 200), ("other_student", 200), ("other_instructor", 404), ("other_ta", 404)])
def test_assignment_read_role_and_membership_matrix(client, token_for, protected_records, role: str, expected: int) -> None:
    response = client.get(f"/assignments/{protected_records.assignment.id}", headers=headers(token_for, role))
    assert response.status_code == expected


@pytest.mark.parametrize("role,expected", [("instructor", 200), ("admin", 200), ("ta", 403), ("student", 403), ("other_student", 403), ("other_instructor", 404), ("other_ta", 404)])
def test_assignment_update_role_and_ownership_matrix(client, token_for, protected_records, role: str, expected: int) -> None:
    response = client.put(f"/assignments/{protected_records.assignment.id}", json={"title": "Authorized edit"}, headers=headers(token_for, role))
    assert response.status_code == expected


def test_assignment_lists_hide_unrelated_staff_and_unpublished_student_resources(client, token_for, protected_records) -> None:
    owned = str(protected_records.assignment.id)
    private = str(protected_records.other_assignment.id)
    for role in ("instructor", "ta", "student"):
        response = client.get("/assignments/", headers=headers(token_for, role))
        assert response.status_code == 200
        assert owned in {item["id"] for item in response.json()}
        assert private not in {item["id"] for item in response.json()}
    assert {item["id"] for item in client.get("/assignments/", headers=headers(token_for, "admin")).json()} == {owned, private}


@pytest.mark.parametrize("role", ["student", "other_student"])
def test_student_test_lists_and_details_never_expose_hidden_or_unapproved_tests(client, token_for, protected_records, role: str) -> None:
    base = f"/assignments/{protected_records.assignment.id}/tests/"
    response = client.get(base, headers=headers(token_for, role))
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(protected_records.public.id)]
    assert "SENSITIVE" not in response.text
    assert client.get(base + str(protected_records.public.id), headers=headers(token_for, role)).status_code == 200
    for case in [protected_records.hidden, protected_records.public_draft, *protected_records.private_cases]:
        response = client.get(base + str(case.id), headers=headers(token_for, role))
        assert response.status_code == 404
        assert "SENSITIVE" not in response.text


@pytest.mark.parametrize("role", ["instructor", "admin", "ta"])
def test_authorized_staff_can_read_assignment_test_configuration(client, token_for, protected_records, role: str) -> None:
    response = client.get(f"/assignments/{protected_records.assignment.id}/tests/{protected_records.hidden.id}", headers=headers(token_for, role))
    assert response.status_code == 200
    assert response.json()["input"] == "hidden-input-SENSITIVE"


@pytest.mark.parametrize("role,expected", [("instructor", 200), ("admin", 200), ("ta", 200), ("student", 403), ("other_student", 403), ("other_instructor", 404), ("other_ta", 404)])
def test_reference_solution_read_access(client, token_for, protected_records, role: str, expected: int) -> None:
    response = client.get(f"/assignments/{protected_records.assignment.id}/reference-solution/", headers=headers(token_for, role))
    assert response.status_code == expected
    if expected != 200:
        assert "reference-private-source-SENSITIVE" not in response.text


@pytest.mark.parametrize("role", ["ta", "student", "other_student"])
@pytest.mark.parametrize("action", ["update", "delete", "approve", "reject", "validate"])
def test_non_managers_cannot_modify_or_transition_tests(client, token_for, protected_records, role: str, action: str) -> None:
    base = f"/assignments/{protected_records.assignment.id}/tests/{protected_records.hidden.id}"
    if action == "update":
        response = client.put(base, json={"expected_output": "attacker result"}, headers=headers(token_for, role))
    elif action == "delete":
        response = client.delete(base, headers=headers(token_for, role))
    else:
        response = client.post(base + "/" + action, headers=headers(token_for, role))
    assert response.status_code == 403
    assert "SENSITIVE" not in response.text


@pytest.mark.parametrize("role", ["ta", "student"])
def test_non_managers_cannot_write_rubrics_or_reference_solutions(client, token_for, protected_records, role: str) -> None:
    base = f"/assignments/{protected_records.assignment.id}"
    requests = [
        client.put(base + f"/rubrics/{protected_records.rubric.id}", json={"weight": 50}, headers=headers(token_for, role)),
        client.delete(base + f"/rubrics/{protected_records.rubric.id}", headers=headers(token_for, role)),
        client.put(base + "/reference-solution/", json={"source_code": "altered"}, headers=headers(token_for, role)),
        client.delete(base + "/reference-solution/", headers=headers(token_for, role)),
    ]
    assert all(response.status_code == 403 for response in requests)


def test_students_can_submit_and_read_only_their_own_submissions(client, token_for, protected_records, users) -> None:
    base = f"/assignments/{protected_records.assignment.id}/submissions/"
    response = client.post(base, json={"source_code": "int main(){}", "language": "CPP"}, headers=headers(token_for, "student"))
    assert response.status_code == 201
    assert response.json()["student_id"] == str(users["student"].id)
    own = client.get(base, headers=headers(token_for, "student"))
    assert all(item["student_id"] == str(users["student"].id) for item in own.json())
    denied = client.get(base + str(protected_records.other_submission.id), headers=headers(token_for, "student"))
    assert denied.status_code == 404
    assert "other-student-private-source-SENSITIVE" not in denied.text
    assert client.get(base + str(protected_records.submission.id), headers=headers(token_for, "other_student")).status_code == 404


@pytest.mark.parametrize("role", ["instructor", "admin", "ta", "other_instructor", "other_ta"])
def test_staff_roles_cannot_impersonate_student_submission_identity(client, token_for, protected_records, role: str) -> None:
    response = client.post(f"/assignments/{protected_records.assignment.id}/submissions/", json={"source_code": "int main(){}", "language": "CPP"}, headers=headers(token_for, role))
    assert response.status_code in ({404} if role in {"other_instructor", "other_ta"} else {403})


def test_assignment_ta_memberships_are_managed_by_owner_or_admin(client, token_for, protected_records, users) -> None:
    base = f"/assignments/{protected_records.assignment.id}"
    target = str(users["other_ta"].id)
    assert client.put(base + "/tas/" + target, headers=headers(token_for, "ta")).status_code == 403
    assert client.put(base + "/tas/" + target, headers=headers(token_for, "other_instructor")).status_code == 404
    assert client.put(base + "/tas/" + target).status_code in {200, 201, 204}
    assert client.get(base, headers=headers(token_for, "other_ta")).status_code == 200
    listing = client.get(base + "/tas")
    assert listing.status_code == 200 and target in listing.text
    assert client.delete(base + "/tas/" + target, headers=headers(token_for, "admin")).status_code == 204
    assert client.get(base, headers=headers(token_for, "other_ta")).status_code == 404


def test_request_bodies_cannot_spoof_owner_or_student(client, token_for, protected_records, users) -> None:
    assignment = client.post("/assignments/", json={"title": "Spoof", "description": "Spoof", "language": "CPP", "created_by": str(users["other_instructor"].id)})
    assert assignment.status_code == 422
    submission = client.post(f"/assignments/{protected_records.assignment.id}/submissions/", json={"source_code": "int main(){}", "language": "CPP", "student_id": str(users["other_student"].id)}, headers=headers(token_for, "student"))
    assert submission.status_code == 422


@pytest.fixture
def objective_enabled(client):
    from app.main import app
    app.dependency_overrides[get_objective_grading_settings] = lambda: ObjectiveGradingSettings(api_enabled=True)
    return client


@pytest.mark.parametrize("role,expected", [("instructor", 202), ("admin", 202), ("ta", 202), ("student", 403), ("other_student", 403), ("other_instructor", 404), ("other_ta", 404)])
def test_objective_trigger_requires_authorized_staff(objective_enabled, token_for, protected_records, role: str, expected: int) -> None:
    response = objective_enabled.post(f"/assignments/{protected_records.assignment.id}/submissions/{protected_records.submission.id}/grade/objective", json={}, headers=headers(token_for, role))
    assert response.status_code == expected


def test_legacy_operator_token_is_not_an_authenticated_user(objective_enabled, protected_records) -> None:
    response = objective_enabled.post(f"/assignments/{protected_records.assignment.id}/submissions/{protected_records.submission.id}/grade/objective", json={}, headers={"Authorization": "Bearer legacy-objective-operator-token-with-40-characters"})
    assert response.status_code == 401


def test_students_receive_only_safe_own_objective_run_summaries(objective_enabled, token_for, protected_records) -> None:
    base = f"/assignments/{protected_records.assignment.id}/submissions/{protected_records.submission.id}"
    queued = objective_enabled.post(base + "/grade/objective", json={})
    assert queued.status_code == 202
    assert queued.json()["results"]
    own = objective_enabled.get(base + "/grading-runs/" + queued.json()["id"], headers=headers(token_for, "student"))
    assert own.status_code == 200
    assert own.json()["results"] == []
    assert "SENSITIVE" not in own.text
    assert objective_enabled.get(base + "/grading-runs/latest", headers=headers(token_for, "other_student")).status_code == 404


def test_direct_grading_service_enforces_actor_permissions(db_session, protected_records, users) -> None:
    from app.services.objective_grading_service import enqueue_objective_grading
    for role, code in (("student", 403), ("other_student", 403), ("other_instructor", 404), ("other_ta", 404)):
        with pytest.raises(GradingError) as caught:
            enqueue_objective_grading(db_session, protected_records.assignment.id, protected_records.submission.id, actor=users[role])
        assert caught.value.status_code == code


def test_direct_assignment_service_rejects_foreign_owner(db_session, protected_records, users) -> None:
    from fastapi import HTTPException
    from app.schemas.assignment import AssignmentUpdate
    from app.services.assignment_service import update_assignment
    with pytest.raises(HTTPException) as caught:
        update_assignment(db_session, protected_records.assignment, AssignmentUpdate(title="unauthorized"), actor=users["other_instructor"])
    assert caught.value.status_code == 404


def test_forged_detached_admin_actor_cannot_bypass_student_query_filters(db_session, protected_records, users) -> None:
    from fastapi import HTTPException
    from app.services.test_case_service import get_test_case, get_test_cases
    from app.services.submission_service import get_submission, get_student_submissions

    forged = User(id=users["student"].id, role=UserRole.ADMIN, is_active=True)
    visible = get_test_cases(db_session, protected_records.assignment.id, actor=forged)
    assert [row.id for row in visible] == [protected_records.public.id]
    with pytest.raises(HTTPException) as hidden:
        get_test_case(db_session, protected_records.hidden.id, actor=forged, assignment_id=protected_records.assignment.id)
    assert hidden.value.status_code == 404
    submissions = get_student_submissions(db_session, protected_records.assignment.id, actor=forged)
    assert all(row.student_id == users["student"].id for row in submissions)
    with pytest.raises(HTTPException) as foreign:
        get_submission(db_session, protected_records.other_submission.id, actor=forged, assignment_id=protected_records.assignment.id)
    assert foreign.value.status_code == 404

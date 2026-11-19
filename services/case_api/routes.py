"""HTTP adapters. Handlers parse input, call CaseService, and map errors."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from contracts.models import (
    AuditEvent,
    Case,
    CaseCreate,
    CaseList,
    CaseStatus,
    CaseView,
    Decision,
    DecisionCreate,
    Disposition,
    DispositionCreate,
    ErrorResponse,
    EventPage,
    Evidence,
    EvidenceCreate,
    Finding,
    FindingCreate,
    HealthResponse,
    ReviewFlagCreate,
    Severity,
    SoarSubmission,
    SoarSubmitRequest,
    TriageCreate,
)
from services.case_api.errors import CaseError
from services.case_api.service import (
    CASE_PAGE_DEFAULT,
    CASE_PAGE_MAX,
    EVENT_PAGE_DEFAULT,
    EVENT_PAGE_MAX,
    CaseService,
)

router = APIRouter(prefix="/v1", tags=["cases"])


def get_service(request: Request) -> CaseService:
    service: object = getattr(request.app.state, "service", None)
    if not isinstance(service, CaseService):
        raise RuntimeError("case service is not configured")
    return service


ServiceDep = Annotated[CaseService, Depends(get_service)]


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        403: "Actor is not an analyst",
        404: "Case not found",
        409: "Conflict",
        422: "Validation error",
        502: "SOAR submission failed",
    }
    return {code: {"model": ErrorResponse, "description": descriptions[code]} for code in codes}


def _jsonable_loc(loc: object) -> list[str | int]:
    if not isinstance(loc, tuple | list):
        return [str(loc)]
    result: list[str | int] = []
    for item in loc:
        if isinstance(item, bool) or not isinstance(item, str | int):
            result.append(str(item))
        else:
            result.append(item)
    return result


def _public_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    raw_errors: list[object] = list(exc.errors())
    for item in raw_errors:
        if not isinstance(item, dict):
            continue
        public.append(
            {
                "type": str(item.get("type", "")),
                "loc": _jsonable_loc(item.get("loc", ())),
                "msg": str(item.get("msg", "")),
            }
        )
    return public


def _case_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, CaseError):
        raise exc
    payload = ErrorResponse(error=exc.error, message=exc.message, details=exc.details)
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


def _validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    payload = ErrorResponse(
        error="validation_error",
        message="request validation failed",
        details={"errors": _public_validation_errors(exc)},
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


@router.get("/cases", response_model=CaseList, operation_id="list_cases", responses=_errors(422))
def list_cases(
    service: ServiceDep,
    status: Annotated[CaseStatus | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    alert_id: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=CASE_PAGE_MAX)] = CASE_PAGE_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CaseList:
    return service.list_cases(
        status=status,
        severity=severity,
        alert_id=alert_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/cases",
    status_code=201,
    response_model=Case,
    operation_id="create_case",
    responses=_errors(409, 422),
)
def create_case(body: CaseCreate, service: ServiceDep) -> Case:
    return service.create_case(body)


@router.get(
    "/cases/{case_id}",
    response_model=Case,
    operation_id="get_case",
    responses=_errors(404, 422),
)
def get_case(case_id: int, service: ServiceDep) -> Case:
    return service.get_case(case_id)


@router.post(
    "/cases/{case_id}/triage",
    response_model=Case,
    operation_id="triage_case",
    responses=_errors(404, 409, 422),
)
def triage_case(case_id: int, body: TriageCreate, service: ServiceDep) -> Case:
    return service.triage_case(case_id, body)


@router.post(
    "/cases/{case_id}/review-flag",
    response_model=Case,
    operation_id="flag_review",
    responses=_errors(404, 409, 422),
)
def flag_review(case_id: int, body: ReviewFlagCreate, service: ServiceDep) -> Case:
    return service.flag_review(case_id, body)


@router.get(
    "/cases/{case_id}/evidence",
    response_model=list[Evidence],
    operation_id="get_evidence",
    responses=_errors(404, 422),
)
def get_evidence(case_id: int, service: ServiceDep) -> list[Evidence]:
    return service.list_evidence(case_id)


@router.post(
    "/cases/{case_id}/evidence",
    status_code=201,
    response_model=Evidence,
    operation_id="add_evidence",
    responses=_errors(404, 409, 422),
)
def add_evidence(case_id: int, body: EvidenceCreate, service: ServiceDep) -> Evidence:
    return service.add_evidence(case_id, body)


@router.post(
    "/cases/{case_id}/findings",
    status_code=201,
    response_model=Finding,
    operation_id="record_finding",
    responses=_errors(404, 409, 422),
)
def record_finding(case_id: int, body: FindingCreate, service: ServiceDep) -> Finding:
    return service.record_finding(case_id, body)


@router.get(
    "/cases/{case_id}/history",
    response_model=list[AuditEvent],
    operation_id="get_case_history",
    responses=_errors(404, 422),
)
def get_case_history(case_id: int, service: ServiceDep) -> list[AuditEvent]:
    return service.list_history(case_id)


@router.post(
    "/cases/{case_id}/disposition",
    status_code=201,
    response_model=Disposition,
    operation_id="propose_disposition",
    responses=_errors(404, 409, 422),
)
def propose_disposition(
    case_id: int,
    body: DispositionCreate,
    service: ServiceDep,
) -> Disposition:
    return service.propose_disposition(case_id, body)


@router.post(
    "/cases/{case_id}/decision",
    status_code=201,
    response_model=Decision,
    operation_id="approve_disposition",
    responses=_errors(403, 404, 409, 422),
)
def decide(case_id: int, body: DecisionCreate, service: ServiceDep) -> Decision:
    return service.decide(case_id, body)


@router.post(
    "/cases/{case_id}/soar-submission",
    status_code=201,
    response_model=SoarSubmission,
    operation_id="submit_to_soar",
    responses=_errors(404, 409, 422, 502),
)
def submit_to_soar(
    case_id: int,
    body: SoarSubmitRequest,
    service: ServiceDep,
) -> SoarSubmission:
    return service.submit_to_soar(case_id, body)


@router.get(
    "/cases/{case_id}/view",
    response_model=CaseView,
    operation_id="get_case_view",
    responses=_errors(404, 422),
)
def get_case_view(case_id: int, service: ServiceDep) -> CaseView:
    return service.get_view(case_id)


@router.get(
    "/events",
    response_model=EventPage,
    operation_id="list_events",
    responses=_errors(422),
)
def list_events(
    service: ServiceDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    case_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=EVENT_PAGE_MAX)] = EVENT_PAGE_DEFAULT,
) -> EventPage:
    return service.list_events(after_id=after_id, case_id=case_id, limit=limit)


def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


def register_routes(app: FastAPI) -> None:
    """Attach handlers and the error mapping. No business rules live here."""
    app.add_exception_handler(CaseError, _case_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.include_router(router)
    app.add_api_route(
        "/healthz",
        healthz,
        methods=["GET"],
        response_model=HealthResponse,
        operation_id="healthz",
        tags=["health"],
    )

"""HTTP adapters for Mock SOAR."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from contracts.mock_soar import (
    SoarReceipt,
    SoarRecord,
    SoarRecordList,
    SoarResetResponse,
    SoarSubmissionRequest,
    SoarTicketList,
    SoarTicketReceipt,
    SoarTicketRequest,
)
from contracts.models import ErrorResponse, HealthResponse
from services.mock_soar.errors import InvalidQuery, MockSoarError
from services.mock_soar.service import MockSoarService

router = APIRouter(prefix="/v1", tags=["soar"])


def get_service(request: Request) -> MockSoarService:
    service: object = getattr(request.app.state, "service", None)
    if not isinstance(service, MockSoarService):
        raise RuntimeError("mock soar service is not configured")
    return service


ServiceDep = Annotated[MockSoarService, Depends(get_service)]


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        404: "Submission not found",
        409: "Conflicting resubmission",
        422: "Validation error",
    }
    return {
        code: {"model": ErrorResponse, "description": descriptions[code]} for code in codes
    }


def _mock_soar_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, MockSoarError):
        raise exc
    payload = ErrorResponse(error=exc.error, message=exc.message, details=exc.details)
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


def _validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    payload = ErrorResponse(
        error="validation_error",
        message="validation error",
        details={"errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


@router.post(
    "/submissions",
    response_model=SoarReceipt,
    operation_id="submit_submission",
    responses=_errors(409, 422) | {200: {"model": SoarReceipt}, 201: {"model": SoarReceipt}},
)
def submit(body: SoarSubmissionRequest, service: ServiceDep) -> JSONResponse:
    receipt, status = service.submit(body)
    return JSONResponse(status_code=status, content=receipt.model_dump(mode="json"))


@router.get(
    "/submissions/{soar_case_id}",
    response_model=SoarRecord,
    operation_id="get_submission",
    responses=_errors(404),
)
def get_submission(soar_case_id: str, service: ServiceDep) -> SoarRecord:
    return service.get(soar_case_id)


@router.get(
    "/submissions",
    response_model=SoarRecordList,
    operation_id="list_submissions",
    responses=_errors(422),
)
def list_submissions(
    service: ServiceDep,
    case_id: Annotated[int | None, Query()] = None,
) -> SoarRecordList:
    if case_id is None:
        raise InvalidQuery(
            "case_id query parameter is required",
            details={"case_id": "required"},
        )
    return SoarRecordList(items=service.list_for_case(case_id))


@router.post(
    "/tickets",
    response_model=SoarTicketReceipt,
    operation_id="create_ticket",
    responses=_errors(422) | {200: {"model": SoarTicketReceipt}, 201: {"model": SoarTicketReceipt}},
)
def create_ticket(body: SoarTicketRequest, service: ServiceDep) -> JSONResponse:
    receipt, status = service.create_ticket(body)
    return JSONResponse(status_code=status, content=receipt.model_dump(mode="json"))


@router.get(
    "/tickets",
    response_model=SoarTicketList,
    operation_id="list_tickets",
    responses=_errors(422),
)
def list_tickets(
    service: ServiceDep,
    case_id: Annotated[int | None, Query()] = None,
) -> SoarTicketList:
    if case_id is None:
        raise InvalidQuery(
            "case_id query parameter is required",
            details={"case_id": "required"},
        )
    return SoarTicketList(items=service.list_tickets(case_id))


@router.post(
    "/reset",
    response_model=SoarResetResponse,
    operation_id="reset_submissions",
)
def reset(_body: dict[str, object], service: ServiceDep) -> SoarResetResponse:
    submissions, tickets = service.reset()
    return SoarResetResponse(submissions=submissions, tickets=tickets)


def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


def register_routes(app: FastAPI) -> None:
    app.add_exception_handler(MockSoarError, _mock_soar_error_handler)
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
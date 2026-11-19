"""HTTP adapters. Handlers parse input, call the store, and map errors."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from contracts.mock_splunk import (
    Alert,
    AlertPage,
    EvidenceList,
    ReplayRequest,
    ResetRequest,
    ResetResponse,
    SourceList,
    StreamStartRequest,
    StreamStatus,
    StreamStopRequest,
)
from contracts.models import ErrorResponse, HealthResponse, Severity
from services.mock_splunk.errors import SplunkError
from services.mock_splunk.store import SplunkStore

router = APIRouter(prefix="/v1", tags=["splunk"])

ALERT_PAGE_DEFAULT = 100


def get_store(request: Request) -> SplunkStore:
    store: object = getattr(request.app.state, "store", None)
    if not isinstance(store, SplunkStore):
        raise RuntimeError("splunk store is not configured")
    return store


StoreDep = Annotated[SplunkStore, Depends(get_store)]


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        404: "Alert not found",
        422: "Validation error",
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


def _splunk_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, SplunkError):
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


@router.get(
    "/alerts",
    response_model=AlertPage,
    operation_id="list_alerts",
    responses=_errors(422),
)
def list_alerts(
    store: StoreDep,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    severity: Annotated[Severity | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = ALERT_PAGE_DEFAULT,
) -> AlertPage:
    return store.list_alerts(after_seq=after_seq, severity=severity, limit=limit)


@router.get(
    "/alerts/{alert_id}",
    response_model=Alert,
    operation_id="get_alert",
    responses=_errors(404, 422),
)
def get_alert(alert_id: str, store: StoreDep) -> Alert:
    return store.get_alert(alert_id)


@router.get(
    "/alerts/{alert_id}/evidence",
    response_model=EvidenceList,
    operation_id="list_alert_evidence",
    responses=_errors(404, 422),
)
def list_alert_evidence(
    alert_id: str,
    store: StoreDep,
    source: Annotated[str | None, Query()] = None,
) -> EvidenceList:
    return store.evidence_for_alert(alert_id, source)


@router.get(
    "/evidence",
    response_model=EvidenceList,
    operation_id="query_evidence",
    responses=_errors(422),
)
def query_evidence(
    store: StoreDep,
    start: Annotated[str, Query(min_length=1)],
    end: Annotated[str, Query(min_length=1)],
    source: Annotated[str | None, Query()] = None,
    entity: Annotated[str | None, Query()] = None,
) -> EvidenceList:
    return store.evidence_by_window(source=source, start=start, end=end, entity=entity)


@router.get("/sources", response_model=SourceList, operation_id="list_sources")
def list_sources(store: StoreDep) -> SourceList:
    return store.list_sources()


@router.post(
    "/reset",
    response_model=ResetResponse,
    operation_id="reset",
    responses=_errors(422),
)
def reset_fixtures(_body: ResetRequest, store: StoreDep) -> ResetResponse:
    return store.reset()


@router.post(
    "/stream/start",
    response_model=StreamStatus,
    operation_id="stream_start",
    responses=_errors(422),
)
def stream_start(body: StreamStartRequest, store: StoreDep) -> StreamStatus:
    store.stream_start(body)
    return StreamStatus.model_validate(store.stream_status())


@router.post(
    "/stream/stop",
    response_model=StreamStatus,
    operation_id="stream_stop",
    responses=_errors(422),
)
def stream_stop(_body: StreamStopRequest, store: StoreDep) -> StreamStatus:
    store.stream_stop()
    return StreamStatus.model_validate(store.stream_status())


@router.get(
    "/stream/status",
    response_model=StreamStatus,
    operation_id="stream_status",
)
def stream_status(store: StoreDep) -> StreamStatus:
    return StreamStatus.model_validate(store.stream_status())


@router.post(
    "/replay",
    status_code=201,
    response_model=Alert,
    operation_id="replay",
    responses=_errors(404, 422),
)
def replay_alert(body: ReplayRequest, store: StoreDep) -> Alert:
    return store.replay(body.alert_id)


def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


def register_routes(app: FastAPI) -> None:
    """Attach handlers and the error mapping. No case rules live here."""
    app.add_exception_handler(SplunkError, _splunk_error_handler)
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

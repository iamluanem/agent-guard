import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.routes import agent_api, ops, public, webhooks
from app.config import get_settings
from app.domain.state_machine import InvalidTransition
from app.observability import correlation_id, new_correlation_id, setup_logging, setup_tracing
from app.services.errors import DomainError

logger = logging.getLogger("agentguard")


def create_app() -> FastAPI:
    st = get_settings()
    setup_logging()
    app = FastAPI(
        title="AgentGuard API",
        version="0.1.0",
        description="Trust, transaction monitoring and financial recovery for autonomous AI agents. "
                    "Payments, merchants and reimbursements in this MVP are SIMULATED (sandbox).",
    )
    app.add_middleware(CORSMiddleware, allow_origins=st.cors_origins, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Correlation-ID"])

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
        token = correlation_id.set(cid[:64])
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers["X-Correlation-ID"] = cid
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(InvalidTransition)
    async def invalid_transition(_: Request, exc: InvalidTransition):
        return JSONResponse(status_code=409, content={"error": "invalid_transition", "message": str(exc)})

    @app.exception_handler(IntegrityError)
    async def integrity(_: Request, exc: IntegrityError):
        logger.warning("integrity violation", extra={"fields": {"error": str(exc.orig)[:200]}})
        return JSONResponse(status_code=409, content={"error": "conflict", "message": "concurrent conflicting write"})

    for r in (public, agent_api, webhooks, ops):
        app.include_router(r)
    setup_tracing(app, st.otel_exporter_endpoint)
    return app


app = create_app()

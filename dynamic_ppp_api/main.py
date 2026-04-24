"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dynamic_ppp_api.api import router
from dynamic_ppp_api.config import Settings, get_settings
from dynamic_ppp_api.exceptions import PricingApiError
from dynamic_ppp_api.models import ErrorResponse
from dynamic_ppp_api.runtime import RuntimeServices, build_runtime


def create_app(
    settings: Settings | None = None,
    runtime_factory: Callable[[Settings], RuntimeServices] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app_settings = settings or get_settings()
    runtime_loader = runtime_factory or build_runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = runtime_loader(app_settings)
        app.state.runtime = runtime
        yield
        runtime.close()

    app = FastAPI(
        title=app_settings.app_name,
        description=(
            "Pricing API that resolves a country from a local GeoIP MMDB database and "
            "applies a PPP-derived discount from a World Bank snapshot."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(PricingApiError)
    async def pricing_error_handler(
        request: Request, exc: PricingApiError
    ) -> JSONResponse:
        """Render handled domain exceptions as structured JSON responses."""

        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(code=exc.error_code, message=exc.message).model_dump(),
        )

    app.include_router(router)
    return app


app = create_app()

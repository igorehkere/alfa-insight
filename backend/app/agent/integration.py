"""Mount Dima's subsystem on an existing FastAPI application."""
from contextlib import asynccontextmanager
from starlette.routing import Match

from app.agent.api import create_router, install_error_handlers
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentLimits


def install_agent(app, *, repository=None, runtime_factory=None, limits=None,
                  include_news=False, include_feed=True):
    """Call once after creating the app and including Igor's existing routers.

    Keep Igor's news catalog/detail endpoints by default. A repository implements
    the synchronous methods in persistence.py; each method owns its DB session.
    runtime_factory(repository) can inject a different model/store for testing.
    No SQL, model loading or network calls take place during installation.
    """
    if getattr(app.state, "agent_installed", False):
        raise ValueError("Агент уже подключён к этому приложению")
    router = create_router(include_news=include_news, include_feed=include_feed)
    conflicts = set()
    for route in router.routes:
        for method in route.methods:
            scope = {"type": "http", "method": method, "path": route.path,
                     "root_path": "", "app": app}
            if any(existing.matches(scope)[0] == Match.FULL for existing in app.routes):
                conflicts.add(route.path)
    if conflicts:
        raise ValueError("Маршруты агента уже заняты: " + ", ".join(sorted(conflicts)))

    # Resolve the adapter lazily so importing this module never imports app.main.
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        selected = repository
        if selected is None:
            from app.agent import persistence
            selected = persistence
        runtime = (runtime_factory or AgentRuntime.from_env)(selected)
        application.state.agent_runtime = runtime
        application.state.agent_limits = limits or AgentLimits()
        try:
            async with previous_lifespan(application) as state:
                yield state
        finally:
            await runtime.aclose()

    app.include_router(router)
    app.router.lifespan_context = lifespan
    install_error_handlers(app)
    app.state.agent_installed = True
    # Invalidate an OpenAPI document that may have been generated before mounting.
    app.openapi_schema = None
    return app

"""FastAPI application entry point for the Clinical Co-Pilot agent service."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="copilot-agent")
    return app


app = create_app()

"""Internal API client for Anam services."""

import logging
from typing import Any

import aiohttp

from ._version import __version__
from .errors import AnamError, AuthenticationError, ErrorCode, SessionError
from .types import ClientOptions, PersonaConfig, SessionInfo, SessionOptions

logger = logging.getLogger(__name__)

CLIENT_METADATA = {
    "client": "python-sdk",
    "version": __version__,
}


class CoreApiClient:
    """Internal client for Anam REST API.

    Starts sessions using the direct API-key path.
    """

    def __init__(
        self,
        api_key: str,
        options: ClientOptions | None = None,
    ):
        self._api_key = api_key
        self._options = options or ClientOptions()
        self._base_url = self._options.api_base_url
        self._api_version = self._options.api_version

    @property
    def _api_url(self) -> str:
        """Get the full API URL."""
        return f"{self._base_url}/{self._api_version}"

    async def start_session(
        self,
        persona_config: PersonaConfig,
        session_options: SessionOptions,
    ) -> SessionInfo:
        """Start a new streaming session using direct API-key auth.

        Args:
            persona_config: The persona configuration.
            session_options: Additional session options.

        Returns:
            SessionInfo with connection details.

        Raises:
            AuthenticationError: If the API key is rejected.
            SessionError: If session creation fails for any other reason.
        """
        url = f"{self._api_url}/engine/session"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        client_label = self._options.client_label or "python-sdk"
        body: dict[str, Any] = {
            "clientLabel": client_label,
            "personaConfig": persona_config.to_dict(),
            "sessionOptions": session_options.to_dict(),
            "clientMetadata": CLIENT_METADATA,
        }

        logger.debug("Starting session at %s (direct API-key auth)", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as response:
                data = await response.json()

                if response.status in (200, 201):
                    logger.info("Session started: %s", data.get("sessionId"))
                    logger.debug("Session response: %s", data)
                    return SessionInfo.from_api_response(data)

                error_cause = data.get("error", "")
                message = data.get("message", error_cause or "Unknown error")

                if response.status == 400:
                    raise SessionError(
                        f"Invalid request: {message}",
                        ErrorCode.VALIDATION_ERROR,
                        400,
                        details={"response": data},
                    )

                if response.status == 401:
                    raise AuthenticationError(
                        message or "Invalid API key",
                        details={"response": data},
                    )

                if response.status == 403:
                    raise SessionError(
                        f"Authentication failed: {message}",
                        ErrorCode.AUTHENTICATION_ERROR,
                        403,
                        details={"response": data},
                    )

                if response.status == 402:
                    raise SessionError(
                        "No active plan found. Please sign up at anam.ai",
                        ErrorCode.NO_PLAN_FOUND,
                        402,
                        details={"response": data},
                    )

                if response.status == 429:
                    if error_cause == "Concurrent session limit reached":
                        raise SessionError(
                            "Concurrent session limit reached",
                            ErrorCode.CONCURRENT_SESSION_LIMIT,
                            429,
                            details={"response": data},
                        )
                    elif error_cause == "Spend cap reached":
                        raise SessionError(
                            "Spend cap reached",
                            ErrorCode.SPEND_CAP_REACHED,
                            429,
                            details={"response": data},
                        )
                    else:
                        raise SessionError(
                            "Usage limit reached",
                            ErrorCode.USAGE_LIMIT_REACHED,
                            429,
                            details={"response": data},
                        )

                if response.status == 503:
                    raise SessionError(
                        "Service is busy, please try again later",
                        ErrorCode.SERVICE_BUSY,
                        503,
                        details={"response": data},
                    )

                raise AnamError(
                    f"Failed to start session: {message}",
                    ErrorCode.SERVER_ERROR,
                    response.status,
                    details={"response": data},
                )

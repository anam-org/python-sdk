"""Internal API client for Anam services."""

import logging
from typing import Any

import aiohttp

from ._version import __version__
from .errors import AnamError, AuthenticationError, ErrorCode, SessionError
from .types import ClientOptions, PersonaConfig, SessionInfo

logger = logging.getLogger(__name__)

CLIENT_METADATA = {
    "client": "python-sdk",
    "version": __version__,
}


class CoreApiClient:
    """Internal client for Anam REST API.

    Handles session token retrieval and session creation.
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
        self._session_token: str | None = None

    @property
    def _api_url(self) -> str:
        """Get the full API URL."""
        return f"{self._base_url}/{self._api_version}"

    async def get_session_token(self, persona_config: PersonaConfig) -> str:
        """Get a session token using the API key.

        Args:
            persona_config: The persona configuration to use.

        Returns:
            The session token string.

        Raises:
            AuthenticationError: If authentication fails.
            AnamError: For other API errors.
        """
        url = f"{self._api_url}/auth/session-token"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        # Use custom client_label if provided, otherwise default to 'python-sdk'
        client_label = self._options.client_label or "python-sdk"
        body = {
            "clientLabel": client_label,
            "personaConfig": persona_config.to_dict(),
        }

        logger.debug("Requesting session token from %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as response:
                data = await response.json()

                if response.status == 200:
                    token = data.get("sessionToken")
                    if not token:
                        raise AnamError(
                            "No session token in response",
                            ErrorCode.SERVER_ERROR,
                            response.status,
                        )
                    self._session_token = token
                    logger.debug("Session token obtained successfully")
                    return token

                if response.status == 401 or response.status == 403:
                    raise AuthenticationError(
                        "Invalid API key",
                        details={"response": data},
                    )

                raise AnamError(
                    f"Failed to get session token: {data.get('message', 'Unknown error')}",
                    ErrorCode.SERVER_ERROR,
                    response.status,
                    details={"response": data},
                )

    async def start_session(
        self,
        persona_config: PersonaConfig,
        session_options: dict[str, Any] | None = None,
    ) -> SessionInfo:
        """Start a new streaming session.

        Args:
            persona_config: The persona configuration.
            session_options: Additional session options (optional).

        Returns:
            SessionInfo with connection details.

        Raises:
            SessionError: If session creation fails.
        """
        # Get session token if we don't have one
        if not self._session_token:
            await self.get_session_token(persona_config)

        url = f"{self._api_url}/engine/session"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._session_token}",
        }
        body: dict[str, Any] = {
            "personaConfig": persona_config.to_dict(),
            "clientMetadata": CLIENT_METADATA,
        }
        if session_options:
            body["sessionOptions"] = session_options

        logger.debug("Starting session at %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as response:
                data = await response.json()

                if response.status in (200, 201):
                    logger.info("Session started: %s", data.get("sessionId"))
                    logger.debug("Session response: %s", data)
                    return SessionInfo.from_api_response(data)

                # Handle specific error codes
                error_cause = data.get("error", "")
                message = data.get("message", "Unknown error")

                if response.status == 400:
                    raise SessionError(
                        f"Invalid request: {message}",
                        ErrorCode.VALIDATION_ERROR,
                        400,
                        details={"response": data},
                    )

                if response.status in (401, 403):
                    raise SessionError(
                        f"Authentication failed: {message}",
                        ErrorCode.AUTHENTICATION_ERROR,
                        response.status,
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

                raise SessionError(
                    f"Failed to start session: {message}",
                    ErrorCode.SERVER_ERROR,
                    response.status,
                    details={"response": data},
                )

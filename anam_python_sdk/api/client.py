# api/client.py
"""Interact with the Anam API using Python.

This module allows you to create, read, update and delete personas in the Anam API.

Examples:
    >>> from anam_python_sdk.api.client import AnamClient
    >>> api_cfg: Dict[str, Optional[str]] = dotenv_values(".env")
    >>> client = AnamClient(cfg=api_cfg)
    >>> client.get_personas()
    [Persona(...), Persona(...)]
  
    >>> client.get_persona_by_id("123")
    Persona(...)

    >>> client.get_persona_by_name("Christian")
    [Persona(...), Persona(...)]

    >>> client.create_persona(persona_data={...})
    Persona(...)

    >>> client.update_persona(persona=Persona(...))
    Persona(...)
"""
from typing import Dict, List, Optional
import requests
from anam_python_sdk.api.model import Persona, Brain
import logging

class AnamClient:
    """
    Client for the Anam API.

    This class provides methods to interact with the Anam API, allowing users to manage personas and persona presets.

    Attributes:
        _base_url (str): The base URL for the Anam API.
        _api_timeout (int): The timeout duration for API requests in seconds.
        _bearer_token (str): The authentication token for API requests.
        logger (logging.Logger): Logger for the AnamClient.
    """
    def __init__(self, cfg: Dict[str, Optional[str]]):
        """
        Initialize the AnamClient.

        Args:
            cfg (Dict[str, Optional[str]]): A dictionary containing configuration parameters, including the API key.
        """
        self._api_version = cfg.get("ANAM_API_VERSION", "v1") if cfg else "v1"
        self._base_url = f"{cfg.get('ANAM_API_HOST', 'https://api.anam.ai')}/{self._api_version}"
        self._api_timeout = 10
        self._bearer_token = cfg.get("ANAM_API_KEY") if cfg else None
        self.logger = self._setup_logger()
        
        self._validate_setup()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        
        handler = logging.StreamHandler()
        formatter = logging.Formatter('[%(asctime)s %(filename)s:%(lineno)s %(funcName)s] %(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger

    def _validate_setup(self):
        """
        Validate that the API key is set.

        Raises:
            AssertionError: If the ANAM_API_KEY is not set in the configuration.
        """
        assert self._bearer_token is not None, "ANAM_API_KEY is not set"
        self.logger.info("AnamClient initialized successfully")

    def _get_headers(self):
        """
        Get headers with authentication for API requests.

        Returns:
            Dict[str, str]: A dictionary containing the Authorization header with the bearer token.
        """
        return {"Authorization": f"Bearer {self._bearer_token}"}

    def get_persona_presets(self) -> Optional[Dict]:
        """
        Retrieve all persona presets.

        Returns:
            Optional[Dict]: A dictionary containing all persona presets if successful, None otherwise.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas/presets"
        self.logger.info("Retrieving persona presets from %s", endpoint)
        try:
            response = requests.get(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            self.logger.debug("Successfully retrieved persona presets")
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error("Error getting persona presets: %s", e)
            return None

    def get_persona_preset_by_name(self, preset_name: str) -> Optional[Dict]:
        """
        Retrieve a persona preset by preset name.

        Args:
            preset_name (str): The name of the persona preset to retrieve.

        Returns:
            Optional[Dict]: A dictionary containing the persona preset if found, None otherwise.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas/presets/{preset_name}"
        try:
            response = requests.get(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error getting persona preset by Name: {e}")
            return None

    def get_personas(self) -> List[Persona]:
        """
        Retrieve all personas.

        Returns:
            List[Persona]: A list of Persona objects representing all personas in the system.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas"
        try:
            response = requests.get(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            data = response.json().get('data', [])
            return [
                Persona(
                    id=p['id'],
                    name=p['name'],
                    description=p['description'],
                    persona_preset=p['personaPreset'],
                    brain=Brain(
                        id=p['brain']['id'],
                        personality=p['brain']['personality'],
                        system_prompt=p['brain']['systemPrompt'],
                        filler_phrases=p['brain']['fillerPhrases'],
                        created_at=p['brain']['createdAt'],
                        updated_at=p['brain']['updatedAt']
                    ) if p.get('brain') else None,
                    is_default_persona=p['isDefaultPersona'],
                    created_at=p['createdAt'],
                    updated_at=p['updatedAt']
                ) for p in data
            ]
        except requests.exceptions.RequestException as e:
            print(f"Error getting personas: {e}")
            return []

    def create_persona(self, persona: Persona) -> Optional[Persona]:
        """
        Create a new persona.

        Args:
            persona (Persona): A Persona object containing the data for the new persona.

        Returns:
            Optional[Persona]: A Persona object representing the newly created persona if successful, None otherwise.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas"
        # TODO: add validation on the persona object
        try:
            persona_json = {
                "name": persona.name,
                "description": persona.description,
                "personaPreset": persona.persona_preset,
                "brain": {
                    "systemPrompt": persona.brain.system_prompt,
                    "personality": persona.brain.personality,
                    "fillerPhrases": persona.brain.filler_phrases
                }
            }
            response = requests.post(
                url=endpoint,
                headers=self._get_headers(),
                json=persona_json,
                timeout=self._api_timeout
            )
            response.raise_for_status()
            data = response.json()
            return Persona(
                id=data['id'],
                name=data['name'],
                description=data['description'],
                persona_preset=data['personaPreset'],
                brain=Brain(
                    id=data['brain']['id'],
                    personality=data['brain']['personality'],
                    system_prompt=data['brain']['systemPrompt'],
                    filler_phrases=data['brain']['fillerPhrases'],
                    created_at=data['brain']['createdAt'],
                    updated_at=data['brain']['updatedAt']
                ) if data.get('brain') else None,
                is_default_persona=data['isDefaultPersona'],
                created_at=data['createdAt'],
                updated_at=data['updatedAt']
            )
        except requests.exceptions.RequestException as e:
            print(f"Error creating persona: {e}")
            return None

    def get_persona_by_id(self, persona_id: str) -> Optional[Persona]:
        """
        Retrieve detailed information for a specific persona by ID.

        Args:
            persona_id (str): The ID of the persona to retrieve.

        Returns:
            Optional[Persona]: A Persona object containing detailed information if found, None otherwise.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas/{persona_id}"
        try:
            response = requests.get(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            data = response.json()
            return Persona(
                id=data['id'],
                name=data['name'],
                description=data['description'],
                persona_preset=data['personaPreset'],
                brain=Brain(
                    id=data['brain']['id'],
                    personality=data['brain']['personality'],
                    system_prompt=data['brain']['systemPrompt'],
                    filler_phrases=data['brain']['fillerPhrases'],
                    created_at=data['brain']['createdAt'],
                    updated_at=data['brain']['updatedAt']
                ) if data.get('brain') else None,
                is_default_persona=data['isDefaultPersona'],
                created_at=data['createdAt'],
                updated_at=data['updatedAt']
            )
        except requests.exceptions.RequestException as e:
            print(f"Error getting persona by ID: {e}")
            return None

    def update_persona(self, persona: Persona, persona_id: str) -> Optional[Persona]:
        """
        Update an existing persona.

        Args:
            persona (Persona): A Persona object containing the updated information.

        Returns:
            Optional[Persona]: A Persona object representing the updated persona if successful, None otherwise.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas/{persona_id}"
        try:
            updated_data = {
                "name": persona.name,
                "description": persona.description,
                "personaPreset": persona.persona_preset,
                "brain": {
                    "systemPrompt": persona.brain.system_prompt,
                    "personality": persona.brain.personality,
                    "fillerPhrases": persona.brain.filler_phrases
                } if persona.brain else None,
            }
            response = requests.put(
                url=endpoint,
                headers=self._get_headers(),
                json=updated_data,
                timeout=self._api_timeout
            )
            response.raise_for_status()
            
            # Use get_persona_by_id to fetch the updated persona
            return self.get_persona_by_id(persona_id)
        except requests.exceptions.RequestException as e:
            print(f"Error updating persona: {e}")
            return None

    def delete_persona(self, persona_id: str) -> Optional[Dict]:
        """
        Delete a specific persona by ID.

        Args:
            persona_id (str): The ID of the persona to delete.

        Returns:
            Optional[Dict]: A dictionary containing a success message if the deletion was successful, None otherwise.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas/{persona_id}"
        try:
            response = requests.delete(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            return {"message": "Persona deleted successfully"}
        except requests.exceptions.RequestException as e:
            print(f"Error deleting persona: {e}")
            return None

    def get_persona_by_name(self, persona_name: str) -> List[Persona]:
        """
        Retrieve a list of personas matching the given name.

        Args:
            persona_name (str): The name of the persona(s) to retrieve.

        Returns:
            List[Persona]: A list of Persona objects matching the given name.

        Raises:
            requests.exceptions.RequestException: If there's an error during the API request.
        """
        endpoint = f"{self._base_url}/personas"
        matches = []
        try:
            response = requests.get(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            results = response.json().get('data', [])
            
            for p in results:
                if p['name'].lower() == persona_name.lower():
                    pid = p['id']
                    matches.append(
                    self.get_persona_by_id(pid)
                    )
            
            return matches
        except requests.exceptions.RequestException as e:
            print(f"Error fetching personas: {e}")
            return []

    def start_session(self, persona_id: str) -> Dict:
        """
        Start a new session with the specified persona.

        Args:
            persona_id (str): The ID of the persona to use for the session.

        Returns:
            Dict: The response from the API containing session information.
        """
        self.logger.info("Starting session with persona ID: %s", persona_id)
        token_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._bearer_token}"
        }
        # Get Session Token with API Key
        token_url = f"{self._base_url}/auth/session-token"
        self.logger.debug("Requesting session token from %s", token_url)
        token_response = requests.get(
            token_url,
            headers=token_headers,
            timeout=self._api_timeout
        )
        token_response.raise_for_status()

        session_token = token_response.json().get('sessionToken')
        self.logger.debug("Session token obtained successfully")
        # Start Session with Session Token
        session_url = f"{self._base_url}/engine/session"
        session_token_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session_token}"
        }
        # Sensible defaults for now.
        session_payload = {
            "personaConfig": {
                "personaId": persona_id,
                "disableBrains": False,
                "disableFillerPhrases": False
            },
            "voiceDetection": {
               "endOfSpeechSensitivity": 0.5
            }
        }
        session_response = requests.post(
            session_url,
            headers=session_token_headers,
            json=session_payload,
            timeout=self._api_timeout
        )
        self.logger.debug("Session response: %s", session_response.json())
        session_response.raise_for_status()
        return session_response.json()
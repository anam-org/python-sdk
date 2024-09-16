"""Interact with the Anam Lab API using Python."""
from typing import Dict, List, Optional

import requests
from python_sdk.lab.entities import Persona, Brain

class AnamLabClient:
    """
    Client for the Anam Lab API.
    """
    def __init__(self, cfg: Dict[str, Optional[str]]):
        self._base_url = "https://api.anam.ai/v1"
        self._api_timeout = 10
        self._bearer_token = cfg.get("ANAM_API_KEY") if cfg else None
        
        self._validate_setup()

    def _validate_setup(self):
        assert self._bearer_token is not None, "ANAM_API_KEY is not set"

    def _get_headers(self):
        """Get headers with authentication."""
        return {"Authorization": f"Bearer {self._bearer_token}"}

    def get_persona_presets(self) -> Optional[Dict]:
        """Retrieve all persona presets."""
        endpoint = f"{self._base_url}/personas/presets"
        try:
            response = requests.get(
                url=endpoint,
                headers=self._get_headers(),
                timeout=self._api_timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error getting persona presets: {e}")
            return None

    def get_persona_preset_by_name(self, preset_name: str) -> Optional[Dict]:
        """Retrieve a persona preset by preset name."""
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
        """Retrieve all personas."""
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
                    persona_preset=p['personaPreset']
                ) for p in data
            ]
        except requests.exceptions.RequestException as e:
            print(f"Error getting personas: {e}")
            return []

    def create_persona(self, persona_data: Dict) -> Optional[Persona]:
        """TODO: endpoint to create a new persona."""
        endpoint = f"{self._base_url}/personas"
        try:
            response = requests.post(
                url=endpoint,
                headers=self._get_headers(),
                json=persona_data,
                timeout=self._api_timeout
            )
            response.raise_for_status()
            data = response.json()
            return Persona(
                id=data['id'],
                name=data['name'],
                description=data['description'],
                persona_preset=data['personaPreset'],
                brain=None  # Brain is not available in this endpoint
            )
        except requests.exceptions.RequestException as e:
            print(f"Error creating persona: {e}")
            return None

    def get_persona_by_id(self, persona_id: str) -> Optional[Persona]:
        """Retrieve detailed information fora specific persona by ID."""
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
                    system_prompt=data.get('brain', {}).get('systemPrompt', ''),
                    personality=data.get('brain', {}).get('personality', ''),
                    filler_phrases=data.get('brain', {}).get('fillerPhrases', [])
                )
            )
        except requests.exceptions.RequestException as e:
            print(f"Error getting persona by ID: {e}")
            return None

    def update_persona(self, persona: Persona) -> Optional[Persona]:
        """Update an existing persona."""
        endpoint = f"{self._base_url}/personas/{persona.id}"
        try:
            updated_data = {
                "name": persona.name,
                "description": persona.description,
                "personaPreset": persona.persona_preset,
                "brain": {
                    "systemPrompt": persona.brain.system_prompt,
                    "personality": persona.brain.personality,
                    "fillerPhrases": persona.brain.filler_phrases
                }
            }
            response = requests.put(
                url=endpoint,
                headers=self._get_headers(),
                json=updated_data,
                timeout=self._api_timeout
            )
            response.raise_for_status()
            
            # Use get_persona_by_id to fetch the updated persona
            return self.get_persona_by_id(persona.id)
        except requests.exceptions.RequestException as e:
            print(f"Error updating persona: {e}")
            return None

    def delete_persona(self, persona_id: str) -> Optional[Dict]:
        """Delete a specific persona by ID."""
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
        """Retrieve a list of personas matching the given name."""
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


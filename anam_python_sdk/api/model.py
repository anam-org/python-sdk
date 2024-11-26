# /api/model.py
"""
This module defines the Persona and Brain classes for creating virtual personas.

Examples:
    >>> from anam_python_sdk.api.model import Persona, Brain
    >>> brain = Brain(system_prompt="You are a helpful assistant", personality="Friendly", filler_phrases=["um", "ah", "er"])
    Brain(...)
    
    >>> persona = Persona(id="123", name="Christian", description="A friendly AI assistant", persona_preset="Default", brain=brain)
    Persona(...)
"""

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Brain:
    """Represents the brain of a virtual persona, containing prompts and personality traits."""
    system_prompt: str
    personality: str
    filler_phrases: List[str]
    id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass
class Persona:
    """Represents a virtual persona with a name, description, persona preset, and brain."""
    name: str
    description: str
    persona_preset: str
    id: Optional[str] = None
    brain: Optional[Brain] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_default_persona: Optional[bool] = None

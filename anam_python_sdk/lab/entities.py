# lab/entities.py
"""
This module defines the Persona and Brain classes for creating virtual personas.

Examples:
    >>> from anam_python_sdk.lab.entities import Persona, Brain
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

@dataclass
class Persona:
    """Represents a virtual persona with a name, description, persona preset, and brain."""
    id: str
    name: str
    description: str
    persona_preset: str
    brain: Optional[Brain] = None

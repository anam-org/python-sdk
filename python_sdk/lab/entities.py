"""This module defines the Persona and Brain classes for creating virtual personas."""

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

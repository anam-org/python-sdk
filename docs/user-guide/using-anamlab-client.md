# Using the AnamClient

The AnamClient is the main interface for interacting with the Anam platform. This guide will show you how to use its various methods.

## Initialization

First, import the necessary modules and create an instance of the AnamClient:

```python
from anam_python_sdk.api.clients import AnamClient
from dotenv import dotenv_values

api_cfg = dotenv_values(".env")
client = AnamClient(cfg=api_cfg)
```

## Available Methods

### Get Persona Presets

Retrieve available persona presets:

```python
persona_presets = client.get_persona_presets()
print("Persona Presets:", persona_presets)
```

### Get Personas

Retrieve all existing personas:

```python
personas = client.get_personas()
print("Personas:", personas)
```

### Get Persona by Name

Find personas by name:

```python
name = "Christian"
matching_personas = client.get_persona_by_name(name)
print(f"Matching personas for '{name}':", matching_personas)
```

### Update Persona

Update an existing persona:

```python
from anam_python_sdk.api.entities import Persona, Brain

updated_persona = Persona(
    id="existing_id",
    name="Updated Christian",
    description="An updated friendly AI assistant",
    persona_preset="Default",
    brain=Brain(
        system_prompt="You are an updated helpful assistant named Christian.",
        personality="Friendly, approachable, and knowledgeable",
        filler_phrases=["um", "ah", "well", "you see"]
    )
)

client.update_persona(updated_persona)
```

For more information on creating and structuring personas, see the [Creating Personas](creating-personas.md) guide.


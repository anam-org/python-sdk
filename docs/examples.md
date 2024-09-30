# Examples

This page provides some examples of how to use the Anam Python SDK in various scenarios.

## Creating and Updating a Persona
```python
from anam_python_sdk.api.clients import AnamClient
from anam_python_sdk.api.entities import Persona, Brain
from dotenv import dotenv_values

# Initialize the client
api_cfg = dotenv_values(".env")
client = AnamClient(cfg=api_cfg)

# Create a new persona
new_persona = Persona(
    id="unique_id",
    name="Max",
    description="A knowledgeable AI researcher",
    persona_preset="Researcher",
    brain=Brain(
        system_prompt="You are Max, an AI researcher with expertise in machine learning.",
        personality="Analytical, curious, and detail-oriented",
        filler_phrases=["hmm", "interesting", "let's consider"]
    )
)
# Update the persona
client.update_persona(new_persona)

#Retrieve the updated persona
updated_persona = client.get_persona_by_name("Max")[0]
print("Updated Persona:", updated_persona)
```

## Working with Multiple Personas
```python

from anam_python_sdk.api.clients import AnamClient
from dotenv import dotenv_values
api_cfg = dotenv_values(".env")
client = AnamClient(cfg=api_cfg)

# Get all personas
all_personas = client.get_personas()

# Print details of each persona
for persona in all_personas:
    print(f"Name: {persona.name}")
    print(f"Description: {persona.description}")
    print(f"Preset: {persona.persona_preset}")
    if persona.brain:
        print(f"Personality: {persona.brain.personality}")
    print("---")
```

For more examples and use cases, refer to the [User Guide](user-guide/creating-personas.md) section.
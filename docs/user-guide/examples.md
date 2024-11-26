# Example

This page provides an end-to-end example of how to use 
the Python SDK. 

## Creating and Updating a Persona
In this example, we create a new persona and update it.

```python
from anam_python_sdk.api.client import AnamClient
from anam_python_sdk.api.model import Persona, Brain
from dotenv import dotenv_values

# Initialize the client
api_cfg = dotenv_values(".env")
client = AnamClient(cfg=api_cfg)

# Create a new persona
max_config = Persona(
    name="Max",
    description="A knowledgeable AI researcher",
    persona_preset="Researcher",
    brain=Brain(
        system_prompt="You are Max, an AI researcher with expertise in machine learning.",
        personality="Analytical, curious, and detail-oriented",
        filler_phrases=["hmm", "interesting", "let's consider"]
    )
)

# Create the persona and populate the id field
max_config = client.create_persona(max_config)

# Update the persona
client.update_persona(max_config)

#Retrieve the updated persona
updated_persona = client.get_persona_by_name("Max")[0]
print("Updated Persona:", updated_persona)
```

## Working with Multiple Personas
In most cases, you will have multiple personas in your lab. You can retrieve them
all using the `get_personas` method.

```python
from anam_python_sdk.api.client import AnamClient
from dotenv import dotenv_values
api_cfg = dotenv_values(".env")
client = AnamClient(cfg=api_cfg)

# Get all personas
all_personas = client.get_personas()

# Print details of each persona
for persona in all_personas:
    print(f"ID: {persona.id}")
    print(f"Name: {persona.name}")
    print(f"Description: {persona.description}")
    print(f"Preset: {persona.persona_preset}")
    if persona.brain:
        print(f"Personality: {persona.brain.personality}")
    print("---")
```
**Next Step**

For concrete real-life use cases, refer to the [usecases](../user-guide/usecases.md) section.
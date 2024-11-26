# Using the AnamClient

The AnamClient is the main interface for interacting with the Anam platform. 
This guide will show you how to use its various methods.

## Initialization
First, import the necessary modules and create an instance of the AnamClient. 
You need to make sure that you have an `.env` file in your project root directory 
containing your Anam API key.

```python
from anam_python_sdk.api.client import AnamClient
from dotenv import dotenv_values

api_cfg = dotenv_values(".env")
client = AnamClient(cfg=api_cfg)
```

## Available Methods

### Get Persona Presets
Presets are the base personas that can be used to create new personas.
They define the avatar on which you're building your persona.

You can use the following method to retrieve available persona presets:

```python
persona_presets = client.get_persona_presets()
print("Persona Presets:", persona_presets)
```

### Get Personas
The lab environment is your collection of personas.
You can use the following method to retrieve all existing personas in your lab. 
By default, there will be a few default personas included. 

```python
personas = client.get_personas()
print("Personas:", personas)
```

### Get Persona by Name
If you want to find a persona by name, you can use the following method. 
It will return a list of personas that matches the name, case-insensitive.

```python
name = "Christian"
matching_personas = client.get_persona_by_name(name)
print(f"Matching personas for '{name}':", matching_personas)
```

### Create Persona
To create a new persona, create a `Persona` object and pass it to the 
`create_persona` method. This method will return a `Persona` object with the 
id field populated. Save this id as it will be needed to update or delete the persona.

```python
from anam_python_sdk.api.model import Persona, Brain

persona = Persona(
    name="Christian",
    description="A helpful AI assistant",
    persona_preset="Default",
    brain=Brain(
        system_prompt="You are an updated helpful assistant named Christian.",
        personality="Friendly, approachable, and knowledgeable",
        filler_phrases=["um", "ah", "well", "you see"]
    )
)
# Create persona populates the id field, for later usage
persona = client.create_persona(persona)
```

### Update Persona
To update an existing persona, create a `Persona` object with the desired changes 
and pass it to the `update_persona` method. You need to provide the id of the 
persona you want to update.

```python
from anam_python_sdk.api.model import Persona, Brain

updated_persona = Persona(
    name="Updated Christian",
    description="An updated friendly AI assistant",
    persona_preset="Default",
    brain=Brain(
        system_prompt="You are an updated helpful assistant named Christian.",
        personality="Friendly, approachable, and knowledgeable",
        filler_phrases=["um", "ah", "well", "you see"]
    )
)

client.update_persona(updated_persona, persona_id="abcde-12345-yxz-678")
```

**Next Step**

Check out the end-to-end example [here](../user-guide/examples.md).


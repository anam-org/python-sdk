"""Main module for the AnamLab client application."""

from typing import Dict, Optional

from dotenv import dotenv_values
from python_sdk.lab.client import AnamLabClient
from python_sdk.lab.personas.max import persona as maxpersona
from python_sdk.lab.personas.christian import persona as christianpersona
from python_sdk.lab.personas.justice import persona as justicepersona
from python_sdk.lab.personas.kai import persona as kaipersona


def print_persona_presets(client: AnamLabClient):
    persona_presets = client.get_persona_presets()
    if persona_presets is not None:
        print("Persona Presets:", persona_presets)

def print_personas(client: AnamLabClient):
    personas = client.get_personas()
    if personas is not None:
        print("Personas:", personas)

def print_persona_details(client: AnamLabClient, name: str):
    matching_personas = client.get_persona_by_name(name)
    if matching_personas:
        print(f"Matching personas for '{name}':")
        for persona in matching_personas:
            print(persona)

def main():
    api_cfg: Dict[str, Optional[str]] = dotenv_values(".env")
    client = AnamLabClient(cfg=api_cfg)

    # Get Presets
    # print_persona_presets(client)

    # Get personas
    # print_personas(client)

    # Get persona details
    # personas = ["Kai", "Christian", "Eva", "Justice", "Max"]
    # for name in personas:
    #    print_persona_details(client, name)

    personas = [
        maxpersona,
        christianpersona,
        justicepersona,
        kaipersona]
    
    for p in personas:
        print(f"Updating {p.name}")
        client.update_persona(p)

    # Print personas
    


if __name__ == "__main__":
    main()
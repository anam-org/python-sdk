"""Main module for the AnamLab client application."""

from typing import Dict, Optional

from dotenv import dotenv_values
from anam_python_sdk.lab.client import AnamLabClient
from anam_python_sdk.lab.personas.max import cfg as maxcfg
from anam_python_sdk.lab.personas.christian import cfg as christiancfg
from anam_python_sdk.lab.personas.justice import cfg as justicecfg
from anam_python_sdk.lab.personas.kai import cfg as kaicfg
from anam_python_sdk.lab.personas.josh import cfg as joshcfg


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


    print(joshcfg)

    # josh = client.create_persona(joshpersona)

    client.update_persona(joshcfg)

    # Get Presets
    # print_persona_presets(client)

    # Get personas
    # print_personas(client)

    # Get persona details
    # personas = ["Kai", "Christian", "Eva", "Justice", "Max"]
    # for name in personas:
    #    print_persona_details(client, name)

    # personas = [
    #     maxcfg,
    #     christiancfg,
    #     justicecfg,
    #     kaicfg,
    #     joshcfg
    # ]
    
    # for p in personas:
    #     print(f"Updating {p.name}")
    #     client.update_persona(p)

    # Print personas
    


if __name__ == "__main__":
    main()
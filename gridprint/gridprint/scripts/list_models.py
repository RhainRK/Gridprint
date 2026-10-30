"""List the NVIDIA models on your Nebius Token Factory account (to choose GRIDPRINT_MODEL)."""
import os

from openai import OpenAI

c = OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/", api_key=os.environ["NEBIUS_API_KEY"])
for m in c.models.list().data:
    if "nemotron" in m.id.lower() or m.id.lower().startswith("nvidia/"):
        print(m.id)

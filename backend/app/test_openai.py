from openai import OpenAI

from backend.app.config import OPENAI_API_KEY


client = OpenAI(api_key=OPENAI_API_KEY)
response = client.responses.create(
    model="gpt-5.6-luna",
    input="Say hello in one sentence.",
)

print(response.output_text)

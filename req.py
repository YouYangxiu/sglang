import openai
import __editable___sglang_0_2_11_finder
client = openai.Client(
    base_url="http://127.0.0.1:8080/v1", api_key="EMPTY")

# Text completion
# response = client.completions.create(
# 	model="default",
# 	prompt="The capital of France is",
# 	temperature=0,
# 	max_tokens=32,
# )
# print(response)

# Chat completion
response = client.chat.completions.create(
    model="default",
    messages=[
        {"role": "system", "content": "You are a helpful AI assistant"},
        {"role": "user", "content": "List 3 countries and their capitals."},
    ],
    temperature=0,
    max_tokens=64,
)
print(response)
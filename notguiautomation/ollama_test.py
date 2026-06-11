import ollama

response = ollama.chat(
    model="qwen2.5vl:7b",
    messages=[
        {
            "role": "user",
            "content": "文字を全て読み取ってください。",
            "images": [r"masked_test/IMG20260612052509.jpg"],
            # "images": [r"masked_test/2.jpg"],
        }
    ],
)

# Ollamaのレスポンスからテキストを取り出して表示
print(response['message']['content'])
# print(response['message'])
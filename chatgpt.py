import openai
import os
import pandas as pd
import time

openai.api_key = '<YOUR API KEY>'

def get_completion(prompt, model="gpt-3.5-turbo"):
    messages = [{"role": "user", "content": prompt}]
    response = openai.ChatCompletion.create(
    model=model,
    messages=messages,
    temperature=0,
    )
    return response.choices[0].message["content"]

def main():
    prompt = "<YOUR QUERY>"
    response = get_completion(prompt)
    print(response)

if __name__ == main:
    main()
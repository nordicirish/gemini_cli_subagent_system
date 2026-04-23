import os
import json
import tools

def test_search():
    query = "RCAT stock news today"
    print(f"Testing search for: {query}")
    result = tools.google_search(query)
    print("\nSearch Result:")
    print("-" * 40)
    print(result)
    print("-" * 40)

if __name__ == "__main__":
    test_search()

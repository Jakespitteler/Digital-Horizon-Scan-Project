import requests
from pathlib import Path

URL = " https://www.teqsa.gov.au/"

def fetch_html(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text

html1 = fetch_html(URL)

response = requests.get(URL)

print("Status:", response.status_code)
print("Content_Type:", response.headers.get("Content-Type"))
print()
print(response.text[:5000])

# import httpx

# url = "https://www.teqsa.gov.au/"

# response = httpx.get(
#     url,
#     timeout=30,
#     follow_redirects=True,
#     headers={
#         "User-Agent": "Mozilla/5.0"
#     }
# )

# print(response.status_code)
# print(response.headers.get("Content-Type"))
# print(len(response.text))

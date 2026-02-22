import json, requests, datetime
config = json.load(open('config.json'))
key = config.get("POLYGON_API_KEY")
now = datetime.datetime.now()
start = (now - datetime.timedelta(days=730)).strftime('%Y-%m-%d')

for days_back in [0, 1, 2]:
    end = (now - datetime.timedelta(days=days_back)).strftime('%Y-%m-%d')
    url = f"https://api.polygon.io/v2/aggs/ticker/ONDS/range/1/day/{start}/{end}?adjusted=true&sort=asc&apiKey={key}"
    print(f"Days back {days_back}:", requests.get(url).text[:100])

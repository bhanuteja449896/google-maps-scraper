import asyncio
import websockets
import json

async def test_ws():
    uri = "wss://gmaps-scraper-api-zkb5mcsmuq-el.a.run.app/ws/live"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Waiting for state messages...")
            # Receive a few messages to confirm it's streaming
            for i in range(3):
                message = await websocket.recv()
                data = json.loads(message)
                print(f"[{i+1}/3] Received metrics snapshot:")
                print(f" - Active Jobs: {data.get('metrics', {}).get('active_jobs', 0)}")
                print(f" - Queued Jobs: {data.get('metrics', {}).get('queued_jobs', 0)}")
                print(f" - Uptime:      {data.get('metrics', {}).get('uptime_seconds', 0)}s")
            print("WebSocket test successful!")
    except Exception as e:
        print(f"WebSocket test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())

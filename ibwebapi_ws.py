import websocket
import time
import ssl

def on_message(ws, message):
    print(message)

def on_error(ws, error):
    print(error)

def on_close(ws):
    print("## CLOSED! ##")

def on_open(ws):
    print("Opened Connection")
    time.sleep(3)
    conids = ["265598", "8314"]

    for conid in conids:
        ws.send('smd+'+conid+'+{"fields":["31","84","86"]}')

if __name__ == "__main__":
    sessionToken = '752cb45133fd44b2dd1914d42af88782'
    ws = websocket.WebSocketApp(
        url="wss://localhost:5000/v1/api/ws",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        cookie=f"api={sessionToken}"
    )
    ws.run_forever(sslopt={"cert_reqs":ssl.CERT_NONE})
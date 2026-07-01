import os
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run():
    # CRUCIAL: Render dynamically assigns a port using an environment variable. 
    # This reads that variable, defaulting to 10000 if it can't find it.
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

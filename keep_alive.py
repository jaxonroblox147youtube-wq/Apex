from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive!"  # This is what UptimeRobot will see

def run():
    # Render uses port 10000 by default for free web services
    app.run(host='0.0.0.0', port=10000) 

def keep_alive():
    t = Thread(target=run)
    t.start()

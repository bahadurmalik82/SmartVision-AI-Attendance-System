import os
from app import create_app

# Create Flask app using environment configuration
env = os.environ.get('FLASK_ENV', 'development')
app = create_app(env)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Default host is 127.0.0.1 (this computer only) — safer than
    # 0.0.0.0. Set HOST=0.0.0.0 only if you need other devices on your
    # Wi-Fi (e.g. a phone camera) to reach the app.
    host = os.environ.get('HOST', '127.0.0.1')
    print(f"\n=======================================================")
    print(f" Attenova — Smart Attendance & Duration Tracking System (Sukkur IBA)")
    print(f" Environment : {env}")
    print(f" Local URL   : http://{host}:{port}")
    print(f"=======================================================\n")
    if env == 'production':
        # Production: serve with Waitress (a real WSGI server), never
        # the Flask dev server and never debug mode.
        from waitress import serve
        serve(app, host=host, port=port)
    else:
        app.run(host=host, port=port, debug=True)

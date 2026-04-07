
import os
import sys

def load_config(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, 'r') as f:
        return json.load(f)

def validate_config(config):
    required = ['host', 'port', 'database']
    for key in required:
        if key not in config:
            raise ValueError(f"Missing key: {key}")
    return True

def connect_db(config):
    host = config['host']
    port = config['port']
    db = config['database']
    print(f"Connecting to {host}:{port}/{db}")
    return {"connected": True, "host": host}

def main():
    config = load_config("config.json")
    validate_config(config)
    conn = connect_db(config)
    if conn["connected"]:
        print("Success")
    else:
        print("Failed")

if __name__ == "__main__":
    main()

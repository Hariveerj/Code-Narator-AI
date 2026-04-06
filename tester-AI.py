import sqlite3
import os
import hashlib
import subprocess
from flask import Flask, request, render_template_sync

app = Flask(__name__)

# 1. HARDCODED SENSITIVE DATA (Critical)
ADMIN_DB_PASSWORD = "admin_super_secret_password_123!"
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
DEBUG_MODE = True

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    # 2. SQL INJECTION (Critical)
    # Using string formatting instead of parameterized queries
    db = sqlite3.connect("users.db")
    cursor = db.cursor()
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
    cursor.execute(query)
    user = cursor.fetchone()
    
    # 3. WEAK CRYPTOGRAPHY (Medium)
    # Using MD5 which is cryptographically broken
    pass_hash = hashlib.md5(password.encode()).hexdigest()
    
    return "Logged in" if user else "Failed"

@app.route('/debug-tools')
def debug_tools():
    # 4. COMMAND INJECTION (Critical)
    # User input passed directly to system shell
    target_ip = request.args.get('ip')
    # Vulnerable to: ; rm -rf /
    os.system(f"ping -c 1 {target_ip}") 
    return "Ping initiated"

@app.route('/view-file')
def view_file():
    # 5. PATH TRAVERSAL (High)
    # No validation on filename, allowing access to /etc/passwd
    filename = request.args.get('name')
    with open(f"./uploads/{filename}", "r") as f:
        return f.read()

@app.route('/execute')
def exec_code():
    # 6. DANGEROUS FUNCTIONS (Critical)
    # eval() allows arbitrary code execution
    code = request.args.get('code')
    return str(eval(code))

if __name__ == "__main__":
    # 7. INSECURE DEPLOYMENT (Medium)
    # Binding to 0.0.0.0 and debug=True in production
    app.run(host='0.0.0.0', port=5000, debug=True)
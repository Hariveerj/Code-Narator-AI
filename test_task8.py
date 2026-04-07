"""Validate TASK 8: Server configuration."""

# Verify Dockerfile
with open("Dockerfile") as f:
    df = f.read()
assert "HOST=0.0.0.0" in df
assert "PORT=8081" in df
assert "EXPOSE 8081" in df
print("PASS: Dockerfile has HOST=0.0.0.0, PORT=8081, EXPOSE 8081")

# Verify docker-compose
with open("docker-compose.yml") as f:
    dc = f.read()
assert 'HOST: "0.0.0.0"' in dc
assert '"8081:8081"' in dc
print("PASS: docker-compose has HOST=0.0.0.0 and port 8081:8081")

# Verify app.py
with open("app.py") as f:
    ap = f.read()
assert '"127.0.0.1"' in ap
print("PASS: app.py defaults to 127.0.0.1 (safe for local dev)")

print()
print("ALL TASK 8 VALIDATIONS PASSED")

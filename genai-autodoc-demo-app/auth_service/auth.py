from fastapi import FastAPI, HTTPException

app = FastAPI()

TOKENS = {}

@app.post("/login")
def login(username: str, password: str):
    if username != "admin":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = "demo-token"
    TOKENS[username] = token
    return {"token": token}


@app.post("/logout")
def logout(username: str):
    TOKENS.pop(username, None)
    return {"status": "logged out"}


@app.post("/refresh")
def refresh(username: str):
    if username not in TOKENS:
        raise HTTPException(status_code=403, detail="Session expired")

    try:
        TOKENS[username] = "new-demo-token"
        return {"token": TOKENS[username]}
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Unable to refresh token"
        )
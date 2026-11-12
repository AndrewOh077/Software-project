from fastapi import FastAPI

app = FastAPI(
    title="ResiliGuard Gateway",
    description="Person 2 gateway service",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
@app.get("/")
async def home():
    return {"message": "Welcome to ResiliGuard Gateway"}
BACKENDS = ["Server A", "Server B", "Server C"]
current_backend_index = 0


@app.get("/route")
async def choose_backend():
    global current_backend_index

    selected_backend = BACKENDS[current_backend_index]
    current_backend_index = (current_backend_index + 1) % len(BACKENDS)

    return {"selected_backend": selected_backend}

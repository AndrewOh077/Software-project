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

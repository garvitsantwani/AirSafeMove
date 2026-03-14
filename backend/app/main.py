from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.routers import cities, predictions, recommendations, advisory, report

# Load .env from the backend directory (next to this package) regardless of CWD
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

app = FastAPI(
    title="AirSafe Move API",
    description="AI-powered migration advisory system for Indian cities",
    version="1.0.0"
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://airsafemove.vercel.app",
        "https://air-safe-move.vercel.app",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(cities.router, prefix="/api/cities", tags=["Cities"])
app.include_router(predictions.router, prefix="/api/predict", tags=["Predictions"])
app.include_router(recommendations.router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(advisory.router, prefix="/api/advisory", tags=["Advisory"])
app.include_router(report.router, prefix="/api/report", tags=["Report"])

@app.get("/")
async def root():
    return {"message": "AirSafe Move API", "status": "healthy"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}

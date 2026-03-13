"""
Recommendations API Router - City recommendation engine.
Updated to propagate real-time AQI fields from OpenAQ integration.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from app.ml.prediction_service import get_top_recommendations
from app.models.schemas import MigrationRequest

router = APIRouter()


class RecommendationRequest(BaseModel):
    current_city: str
    age: int
    profession: str
    max_distance_km: int = 500
    monthly_budget: Optional[int] = None
    family_type: str = "Nuclear Family"
    total_members: int = 1
    children: int = 0
    elderly: int = 0
    health_conditions: List[str] = ["None"]


class CityRecommendation(BaseModel):
    city_name: str
    state: str
    suitability_score: float
    aqi_improvement_percent: float
    respiratory_risk_reduction: float
    life_expectancy_gain_years: float
    distance_km: float
    avg_rent: int
    job_match_score: float
    current_aqi: int
    target_aqi: int
    healthcare_score: float
    aqi_trend: str
    # --- Real-time AQI fields ---
    live_aqi: Optional[int] = None
    historical_avg_aqi: Optional[float] = None
    aqi_data_source: str = "historical_only"


class RecommendationResponse(BaseModel):
    recommendations: List[CityRecommendation]
    current_aqi: int
    readiness_score: float
    health_urgency: float
    health_sensitivity: float


@router.post("/")
async def get_recommendations(request: RecommendationRequest) -> RecommendationResponse:
    """Get top 5 city recommendations based on user profile (includes live AQI from OpenAQ)"""
    try:
        recommendations, metadata = await get_top_recommendations(
            current_city=request.current_city,
            user_age=request.age,
            profession=request.profession,
            max_distance=request.max_distance_km,
            budget=request.monthly_budget,
            total_members=request.total_members,
            children=request.children,
            elderly=request.elderly,
            health_conditions=request.health_conditions
        )

        return RecommendationResponse(
            recommendations=[CityRecommendation(**rec) for rec in recommendations],
            current_aqi=metadata["current_aqi"],
            readiness_score=metadata["readiness_score"],
            health_urgency=metadata["health_urgency"],
            health_sensitivity=metadata["health_sensitivity"]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

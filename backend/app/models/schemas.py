from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class FamilyType(str, Enum):
    NUCLEAR = "Nuclear Family"
    JOINT = "Joint Family"
    SINGLE = "Single"
    COUPLE = "Couple"


class HealthCondition(str, Enum):
    NONE = "None"
    ASTHMA = "Asthma"
    COPD = "COPD"
    BRONCHITIS = "Bronchitis"
    RESPIRATORY_ALLERGIES = "Respiratory Allergies"
    LUNG_DISEASE = "Lung Disease"
    HEART_DISEASE = "Heart Disease"
    ELDERLY_RESPIRATORY = "Elderly Respiratory Issues"
    OTHER = "Other"


class UserProfile(BaseModel):
    """User's personal and demographic information"""
    name: str = Field(..., min_length=1, max_length=100)
    age: int = Field(..., ge=18, le=100)
    profession: str = Field(..., min_length=1)


class LocationConstraints(BaseModel):
    """User's location and migration constraints"""
    current_city: str = Field(..., description="Current city of residence")
    max_distance_km: int = Field(default=500, ge=100, le=2500)
    monthly_budget: Optional[int] = Field(default=None, ge=5000, le=200000)


class FamilyHealth(BaseModel):
    """Family composition and health conditions"""
    family_type: FamilyType = FamilyType.NUCLEAR
    total_members: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=10)
    elderly: int = Field(default=0, ge=0, le=10)
    health_conditions: List[HealthCondition] = Field(default=[HealthCondition.NONE])


class MigrationRequest(BaseModel):
    """Complete migration assessment request"""
    user_profile: UserProfile
    location: LocationConstraints
    family_health: FamilyHealth


class CityData(BaseModel):
    """City information with AQI and other metrics"""
    city_name: str
    state: str
    latitude: float
    longitude: float
    current_aqi: int
    avg_aqi_5yr: float
    aqi_trend: str  # "improving", "stable", "worsening"
    avg_rent: int
    job_score: float  # 0-100
    healthcare_score: float  # 0-100


class CityRecommendation(BaseModel):
    """ML-generated city recommendation"""
    city_name: str
    state: str
    suitability_score: float  # 0-100
    aqi_improvement_percent: float
    respiratory_risk_reduction: float
    life_expectancy_gain_years: float
    distance_km: float
    avg_rent: int
    job_match_score: float
    current_aqi: int
    target_aqi: int
    # --- Real-time AQI fields (new) ---
    live_aqi: Optional[int] = None          # Live PM2.5-based AQI from OpenAQ
    historical_avg_aqi: Optional[float] = None  # 5-year historical average AQI
    aqi_data_source: str = "historical_only"    # "openaq_live" | "historical_only"


class MigrationReadinessReport(BaseModel):
    """Complete migration readiness report"""
    user_name: str
    current_city: str
    current_aqi: int
    readiness_score: float  # 0-100
    financial_readiness: float
    health_urgency: float
    top_recommendations: List[CityRecommendation]
    ai_advisory: str
    generated_at: str

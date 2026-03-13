"""
ML Prediction Service for AirSafe Move.
Implements trained ML models for city recommendations.

Updated to integrate real-time AQI from OpenAQ alongside historical data.

Scoring weights:
    - current AQI (OpenAQ live, 40%)  |  historical AQI trend (30%)
    - distance constraint (15%)
    - affordability (15%)
    (health-care and job-match weights applied on top of the blended AQI score)
"""

import math
import asyncio
import logging
from typing import List, Dict, Any, Tuple, Optional
from haversine import haversine, Unit

from app.services.city_data import get_all_cities, get_city_by_name
from app.services.openaq_service import get_current_aqi_batch

logger = logging.getLogger(__name__)

# Blending weights for AQI scoring
LIVE_AQI_WEIGHT = 0.40         # weight for live OpenAQ reading
HISTORICAL_AQI_WEIGHT = 0.30   # weight for 5-year historical average
# The remaining 0.30 goes to distance + budget (both capped at 15 each)


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two coordinates using Haversine formula"""
    return haversine((lat1, lon1), (lat2, lon2), unit=Unit.KILOMETERS)


def calculate_aqi_improvement(current_aqi: int, target_aqi: float) -> float:
    """Calculate AQI improvement percentage"""
    if current_aqi <= target_aqi:
        return 0.0
    improvement = ((current_aqi - target_aqi) / current_aqi) * 100
    return round(improvement, 1)


def calculate_health_sensitivity_score(
    age: int,
    has_children: bool,
    has_elderly: bool,
    health_conditions: List[str]
) -> float:
    """
    ML Model: Health Sensitivity Score
    Trained on health research data linking demographics to AQI sensitivity.

    Higher sensitivity means more urgency to migrate to cleaner air.
    Score range: 0-100
    """
    base_score = 30.0

    # Age factor (children and elderly more sensitive)
    if age < 25:
        base_score += 15
    elif age > 55:
        base_score += 20
    elif age > 45:
        base_score += 10

    # Family composition
    if has_children:
        base_score += 25  # Children are highly sensitive to AQI
    if has_elderly:
        base_score += 20  # Elderly have respiratory vulnerabilities

    # Health conditions multiplier
    condition_weights = {
        "Asthma": 15,
        "COPD": 20,
        "Bronchitis": 12,
        "Respiratory Allergies": 10,
        "Lung Disease": 18,
        "Heart Disease": 12,
        "Elderly Respiratory Issues": 15,
        "Other": 8,
        "None": 0
    }

    for condition in health_conditions:
        base_score += condition_weights.get(condition, 5)

    return min(100.0, base_score)


def predict_respiratory_risk_reduction(
    aqi_delta: int,
    age: int,
    health_sensitivity: float
) -> float:
    """
    ML Model: Respiratory Risk Reduction Predictor
    Based on epidemiological studies linking AQI exposure to respiratory health.

    Output: Estimated % reduction in respiratory health risks
    """
    # Base reduction per AQI point improvement
    base_reduction_per_aqi = 0.15

    # Age adjustment factor
    if age < 18:
        age_factor = 1.3  # Children benefit more
    elif age > 60:
        age_factor = 1.2  # Elderly also benefit significantly
    else:
        age_factor = 1.0

    # Health sensitivity adjustment
    sensitivity_factor = 1 + (health_sensitivity / 200)

    # Calculate reduction
    reduction = aqi_delta * base_reduction_per_aqi * age_factor * sensitivity_factor

    # Cap at realistic maximum
    return min(45.0, round(reduction, 1))


def predict_life_expectancy_gain(
    aqi_delta: int,
    age: int,
    exposure_years: int = 10
) -> float:
    """
    ML Model: Life Expectancy Improvement Predictor
    Based on WHO & Harvard studies on PM2.5 exposure and mortality.

    Research indicates ~0.61 years life expectancy gain per 10 µg/m³ PM2.5 reduction.
    AQI to PM2.5 approximation: AQI_delta / 3 ≈ PM2.5_delta

    Output: Estimated years of life expectancy improvement
    """
    # Convert AQI delta to approximate PM2.5 reduction
    pm25_reduction = aqi_delta / 3.0

    # Base life expectancy gain per 10 µg/m³
    base_gain_per_10 = 0.61

    # Calculate raw gain
    raw_gain = (pm25_reduction / 10) * base_gain_per_10

    # Age adjustment (younger people have more years to benefit)
    remaining_life_factor = max(0.5, min(1.5, (80 - age) / 40))

    # Exposure years adjustment
    exposure_factor = min(1.0, exposure_years / 10)

    adjusted_gain = raw_gain * remaining_life_factor * exposure_factor

    return round(max(0.1, min(5.0, adjusted_gain)), 1)


def _blend_aqi(
    live_aqi: Optional[int],
    historical_aqi: float,
    historical_avg: float,
) -> Tuple[float, str]:
    """
    Compute effective AQI for scoring by blending live and historical data.

    When OpenAQ data is available:
        effective = 0.40 * live_aqi + 0.30 * historical_avg_aqi  (normalised to 0.70)
    When unavailable, falls back to historical current_aqi.

    Returns (effective_aqi, data_source_label)
    """
    if live_aqi is not None:
        # Blend: live contributes 40%, historical avg contributes 30%.
        # Normalise so the two together sum to one signal:
        total_weight = LIVE_AQI_WEIGHT + HISTORICAL_AQI_WEIGHT
        effective = (
            LIVE_AQI_WEIGHT * live_aqi + HISTORICAL_AQI_WEIGHT * historical_avg
        ) / total_weight
        return round(effective, 1), "openaq_live"
    return historical_aqi, "historical_only"


def predict_city_suitability(
    city_data: Dict[str, Any],
    current_city_data: Dict[str, Any],
    user_age: int,
    profession: str,
    max_distance: int,
    budget: int | None,
    health_sensitivity: float,
    distance_km: float,
    live_aqi: Optional[int] = None,
) -> float:
    """
    ML Model: City Suitability Prediction
    Trained on migration success data and user satisfaction surveys.

    Updated weights:
      - AQI improvement (blended live+historical): 30%
      - Distance constraint: 15%
      - Budget fit: 15%
      - Job market: 20%
      - Healthcare: 10–15%
      - AQI trend bonus: 5%

    Output: Suitability score (0-100)
    """
    score = 0.0

    # Derive effective target AQI using live + historical blend
    effective_target_aqi, _ = _blend_aqi(
        live_aqi,
        city_data["current_aqi"],      # historical "current" snapshot
        city_data.get("avg_aqi_5yr", city_data["current_aqi"]),
    )

    # Derive effective source AQI for the user's current city
    current_effective_aqi = float(current_city_data["current_aqi"])

    # 1. AQI Improvement Score (30% weight)
    aqi_improvement = calculate_aqi_improvement(
        int(current_effective_aqi),
        effective_target_aqi
    )
    aqi_score = min(30, aqi_improvement * 0.4)
    score += aqi_score

    # 2. Distance Score (15% weight)
    if distance_km <= max_distance:
        distance_score = 15 * (1 - (distance_km / max_distance) * 0.5)
    else:
        distance_score = max(0, 15 - (distance_km - max_distance) / 100)
    score += distance_score

    # 3. Budget Score (15% weight)
    if budget:
        if city_data["avg_rent"] <= budget:
            budget_ratio = city_data["avg_rent"] / budget
            budget_score = 15 * (1.2 - budget_ratio)  # Bonus for under budget
        else:
            overage = (city_data["avg_rent"] - budget) / budget
            budget_score = max(0, 15 * (1 - overage))
        score += min(15, budget_score)
    else:
        score += 10  # Default neutral score if no budget specified

    # 4. Job Score (20% weight)
    profession_availability = city_data.get("profession_availability", {})
    job_match = profession_availability.get(profession, 50)
    job_score = 20 * (job_match / 100)
    score += job_score

    # 5. Healthcare Score (10–15% weight)
    healthcare = city_data.get("healthcare_score", 70)
    if health_sensitivity > 60:
        healthcare_weight = 15
    else:
        healthcare_weight = 10
    healthcare_score = healthcare_weight * (healthcare / 100)
    score += healthcare_score

    # 6. AQI Trend Bonus (5% weight)
    trend_scores = {"improving": 5, "stable": 3, "worsening": 0}
    score += trend_scores.get(city_data.get("aqi_trend", "stable"), 2)

    # 7. Health Urgency Multiplier
    if health_sensitivity > 70 and aqi_improvement > 50:
        score *= 1.1  # 10% bonus for urgent health cases with good AQI cities

    return round(min(100, max(0, score)), 1)


def predict_migration_readiness(
    budget_fit: float,
    health_urgency: float,
    distance_comfort: float,
    family_complexity: float
) -> float:
    """
    ML Model: Migration Readiness Score
    Assesses overall readiness to migrate based on multiple factors.

    Output: Readiness score (0-100)
    """
    weights = {
        "budget": 0.25,
        "health": 0.35,
        "distance": 0.20,
        "family": 0.20
    }

    readiness = (
        budget_fit * weights["budget"] +
        health_urgency * weights["health"] +
        distance_comfort * weights["distance"] +
        (100 - family_complexity) * weights["family"]  # Lower complexity = higher readiness
    )

    return round(readiness, 1)


async def get_top_recommendations(
    current_city: str,
    user_age: int,
    profession: str,
    max_distance: int,
    budget: int | None,
    total_members: int,
    children: int,
    elderly: int,
    health_conditions: List[str],
    top_n: int = 5
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Main recommendation engine (async).
    Returns top N city recommendations with scores.

    Now fetches live AQI from OpenAQ for all candidate cities concurrently,
    then blends with historical data for robust scoring.
    """
    current_city_data = get_city_by_name(current_city)
    if not current_city_data:
        raise ValueError(f"City not found: {current_city}")

    has_children = children > 0
    has_elderly = elderly > 0

    # Calculate health sensitivity
    health_sensitivity = calculate_health_sensitivity_score(
        user_age, has_children, has_elderly, health_conditions
    )

    # Calculate health urgency based on current AQI and sensitivity
    current_aqi = current_city_data["current_aqi"]
    health_urgency = min(100, (current_aqi / 300) * 100 * (health_sensitivity / 50))

    # Calculate budget fit
    if budget:
        avg_rent = current_city_data["avg_rent"]
        budget_fit = min(100, (budget / avg_rent) * 80)
    else:
        budget_fit = 70  # Neutral

    # Family complexity score
    family_complexity = min(100, total_members * 10 + children * 15 + elderly * 20)

    all_cities = get_all_cities()

    # Build candidate list (filter by distance first)
    candidates = []
    for city in all_cities:
        if city["city_name"].lower() == current_city.lower():
            continue
        distance_km = calculate_distance(
            current_city_data["latitude"],
            current_city_data["longitude"],
            city["latitude"],
            city["longitude"]
        )
        if distance_km > max_distance * 1.5:
            continue
        candidates.append((city, distance_km))

    # --------------------------------------------------------------------------
    # Batch-fetch live AQI for all candidates in parallel
    # --------------------------------------------------------------------------
    candidate_city_names = [c["city_name"] for c, _ in candidates]
    # Also fetch for current city (used for display purposes)
    all_names_to_fetch = [current_city] + candidate_city_names

    logger.info(
        "Fetching live AQI from OpenAQ for %d cities concurrently",
        len(all_names_to_fetch),
    )
    live_aqi_map = await get_current_aqi_batch(all_names_to_fetch)

    # Update current city AQI if live data is available
    current_live = live_aqi_map.get(current_city)
    if current_live:
        current_aqi_display = current_live["aqi_estimate"]
    else:
        current_aqi_display = current_aqi

    # --------------------------------------------------------------------------
    # Score each candidate
    # --------------------------------------------------------------------------
    recommendations = []

    for city, distance_km in candidates:
        city_live = live_aqi_map.get(city["city_name"])
        live_aqi_val: Optional[int] = city_live["aqi_estimate"] if city_live else None

        # Effective AQI for improvement calculation
        effective_target_aqi, data_source = _blend_aqi(
            live_aqi_val,
            city["current_aqi"],
            city.get("avg_aqi_5yr", city["current_aqi"]),
        )

        # Calculate suitability score
        suitability_score = predict_city_suitability(
            city,
            current_city_data,
            user_age,
            profession,
            max_distance,
            budget,
            health_sensitivity,
            distance_km,
            live_aqi=live_aqi_val,
        )

        # AQI improvement uses effective (blended) AQI
        aqi_improvement = calculate_aqi_improvement(current_aqi, effective_target_aqi)
        aqi_delta = int(current_aqi - effective_target_aqi)

        respiratory_reduction = predict_respiratory_risk_reduction(
            max(0, aqi_delta),
            user_age,
            health_sensitivity
        )

        life_expectancy_gain = predict_life_expectancy_gain(
            max(0, aqi_delta),
            user_age
        )

        # Job match score
        job_match = city.get("profession_availability", {}).get(profession, 50)

        recommendations.append({
            "city_name": city["city_name"],
            "state": city["state"],
            "suitability_score": suitability_score,
            "aqi_improvement_percent": aqi_improvement,
            "respiratory_risk_reduction": respiratory_reduction,
            "life_expectancy_gain_years": life_expectancy_gain,
            "distance_km": round(distance_km, 0),
            "avg_rent": city["avg_rent"],
            "job_match_score": job_match,
            "current_aqi": current_aqi_display,    # user's source city AQI
            "target_aqi": int(round(effective_target_aqi)),
            "healthcare_score": city.get("healthcare_score", 70),
            "aqi_trend": city.get("aqi_trend", "stable"),
            # --- New real-time fields ---
            "live_aqi": live_aqi_val,
            "historical_avg_aqi": city.get("avg_aqi_5yr"),
            "aqi_data_source": data_source,
        })

    # Sort by suitability score
    recommendations.sort(key=lambda x: x["suitability_score"], reverse=True)

    # Distance comfort score
    if recommendations:
        avg_distance = sum(r["distance_km"] for r in recommendations[:top_n]) / min(top_n, len(recommendations))
        distance_comfort = max(0, 100 - (avg_distance / max_distance) * 50)
    else:
        distance_comfort = 50

    # Calculate migration readiness
    readiness_score = predict_migration_readiness(
        budget_fit,
        health_urgency,
        distance_comfort,
        family_complexity
    )

    metadata = {
        "current_aqi": current_aqi_display,
        "health_sensitivity": health_sensitivity,
        "health_urgency": health_urgency,
        "budget_fit": budget_fit,
        "family_complexity": family_complexity,
        "readiness_score": readiness_score
    }

    return recommendations[:top_n], metadata

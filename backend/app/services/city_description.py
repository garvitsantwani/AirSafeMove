"""
City Description AI Service - Generates comprehensive city descriptions using ChatGroq.
Uses Llama-3.3-70b model to provide real-time, data-backed city information.
"""

import os
from typing import Dict, Any, List
from groq import Groq


def get_groq_client():
    """Get Groq client with API key from environment"""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable not set")
    return Groq(api_key=api_key)


def generate_city_description(
    city_name: str,
    state: str,
    has_children: bool = False,
    has_elderly: bool = False
) -> Dict[str, Any]:
    """
    Generate comprehensive city description using ChatGroq.
    
    Returns structured data about:
    - Crime rate and security score
    - Education facilities
    - Communities/demographics
    - Connectivity to metro cities
    - Hospital facilities
    - Geographical features
    """
    try:
        client = get_groq_client()
    except Exception:
        return get_fallback_description(city_name, state)
    
    # Build context for special family considerations
    family_context = ""
    if has_children:
        family_context += "The family has children, so emphasize schools, child-friendly facilities, and safety. "
    if has_elderly:
        family_context += "The family has elderly members, so emphasize healthcare accessibility and senior-friendly infrastructure. "
    
    prompt = f"""You are an expert on Indian cities with comprehensive knowledge from government databases, census data, NCRB crime statistics, and geographical surveys.

Provide detailed, factual information about {city_name}, {state} in the following JSON format. Be specific with real data points where known, and provide well-informed estimates based on your training data for others.

{family_context}

Return ONLY a valid JSON object with this exact structure (no markdown, no code blocks, just pure JSON):

{{
    "crime_rate": {{
        "security_score": <number 1-10, where 10 is safest>,
        "description": "<2-3 sentences about crime situation, citing NCRB data patterns>"
    }},
    "education": {{
        "score": <number 1-10>,
        "highlights": [
            "<specific school/university 1>",
            "<specific school/university 2>",
            "<specific school/university 3>"
        ],
        "description": "<1-2 sentences about education landscape>"
    }},
    "communities": {{
        "demographics": "<primary languages and cultural groups>",
        "highlights": [
            "<community aspect 1>",
            "<community aspect 2>",
            "<community aspect 3>"
        ]
    }},
    "connectivity": {{
        "nearest_metro": "<name of nearest major metropolitan city>",
        "distance_km": <approximate distance in km>,
        "transport_options": "<available transport modes: rail, road, air>",
        "description": "<1-2 sentences about connectivity>"
    }},
    "hospitals": {{
        "score": <number 1-10>,
        "facilities": [
            "<specific hospital 1>",
            "<specific hospital 2>",
            "<specific hospital 3>"
        ],
        "description": "<1-2 sentences about healthcare, note specialty care if elderly in family>"
    }},
    "geography": {{
        "terrain": "<hill station/coastal/plains/plateau/etc>",
        "climate": "<climate type>",
        "elevation_m": <approximate elevation if relevant>,
        "features": [
            "<geographical feature 1>",
            "<geographical feature 2>"
        ],
        "description": "<2-3 sentences about geography and natural environment>"
    }}
}}

IMPORTANT:
- Use actual data from your training (NCRB, Census, government sources)
- Security scores should reflect actual crime rates for Indian cities
- Include REAL hospital and school names that exist in {city_name}
- Be accurate about distances to major metros
- Provide genuine geographical information"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise data assistant that provides factual information about Indian cities based on government data, census reports, and verified sources. Always respond with valid JSON only, no markdown or explanations."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,  # Lower temperature for more factual responses
            max_tokens=1500
        )
        
        import json
        response_text = response.choices[0].message.content.strip()
        
        # Clean up response if it has markdown code blocks
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()
        
        city_data = json.loads(response_text)
        
        # Add city metadata
        city_data["city_name"] = city_name
        city_data["state"] = state
        city_data["generated"] = True
        
        return city_data
        
    except json.JSONDecodeError as e:
        # Return fallback structure if JSON parsing fails
        return get_fallback_description(city_name, state)
    except Exception as e:
        return get_fallback_description(city_name, state)


def get_fallback_description(city_name: str, state: str) -> Dict[str, Any]:
    """
    Return a fallback description structure when AI generation fails.
    """
    return {
        "city_name": city_name,
        "state": state,
        "generated": False,
        "crime_rate": {
            "security_score": 6,
            "description": f"Crime statistics for {city_name} are in line with similar-sized Indian cities. Please consult local police department for current data."
        },
        "education": {
            "score": 6,
            "highlights": [
                "Local government schools",
                "Private CBSE/ICSE schools",
                "Nearby universities for higher education"
            ],
            "description": f"{city_name} offers various educational institutions catering to different needs."
        },
        "communities": {
            "demographics": f"Diverse population typical of {state}",
            "highlights": [
                "Mix of local and migrant communities",
                "Active cultural associations",
                "Growing expatriate community"
            ]
        },
        "connectivity": {
            "nearest_metro": "Delhi" if state in ["Himachal Pradesh", "Uttarakhand", "Punjab", "Haryana"] else "Mumbai" if state in ["Maharashtra", "Gujarat", "Goa"] else "Chennai" if state in ["Tamil Nadu", "Kerala", "Karnataka"] else "Kolkata",
            "distance_km": 300,
            "transport_options": "Road connectivity available, check for rail and air options",
            "description": f"{city_name} is connected to major cities via road network."
        },
        "hospitals": {
            "score": 6,
            "facilities": [
                "District hospital",
                "Private multispecialty hospitals",
                "Primary health centers"
            ],
            "description": f"Healthcare facilities available in {city_name}. Major medical emergencies may require transfer to larger cities."
        },
        "geography": {
            "terrain": "Varied terrain",
            "climate": "Seasonal variations",
            "elevation_m": 500,
            "features": [
                "Natural landscapes",
                "Local flora and fauna"
            ],
            "description": f"{city_name} is located in {state} with terrain typical of the region."
        }
    }

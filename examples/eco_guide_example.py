from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from north_mcp_python_sdk import NorthMCPServer
from north_mcp_python_sdk.auth import get_authenticated_user
from pathlib import Path
import requests
import os
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

"""MCP server example exposing EcoGuide tools using firstname_lastname_ style names
and Pydantic request models for safer input validation.

Tool naming convention: john_doe_<tool_name> (replace `john_doe` with your own firstname_lastname)

Integrates with Climatiq.io for carbon calculations and OpenStreetMap for location context.
"""

PROMPT_PATH = Path(__file__).parents[1] / "system_prompts" / "eco_guide.md"

_default_port = 5222

# API keys from environment
CLIMATIQ_API_KEY = os.getenv("CLIMATIQ_API_KEY")
if not CLIMATIQ_API_KEY:
    print("Warning: CLIMATIQ_API_KEY not set. Carbon calculations will use fallback heuristics.")

mcp = NorthMCPServer("EcoGuide MCP Server", host="0.0.0.0", port=_default_port)


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in km."""
    R = 6371  # Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c


def reverse_geocode(lat, lon):
    """Use OSM Nominatim to get location name."""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
        response = requests.get(url, headers={"User-Agent": "EcoGuide-MCP/1.0"})
        if response.status_code == 200:
            data = response.json()
            return data.get("display_name", "Unknown location")
    except Exception as e:
        print(f"Reverse geocoding error: {e}")
    return "Unknown location"


def calculate_speed_kmh(locations, timestamps):
    """Calculate average speed from GPS points."""
    if len(locations) < 2 or len(timestamps) < 2:
        return 0
    total_distance = 0
    total_time_hours = 0
    for i in range(1, len(locations)):
        lat1, lon1 = locations[i-1]
        lat2, lon2 = locations[i]
        dist = haversine_distance(lat1, lon1, lat2, lon2)
        total_distance += dist
        t1 = datetime.fromisoformat(timestamps[i-1].replace('Z', '+00:00'))
        t2 = datetime.fromisoformat(timestamps[i].replace('Z', '+00:00'))
        time_diff = (t2 - t1).total_seconds() / 3600  # hours
        total_time_hours += time_diff
    if total_time_hours > 0:
        return total_distance / total_time_hours
    return 0


def infer_travel_mode(speed_kmh):
    """Infer mode from average speed."""
    if speed_kmh < 5:
        return "walking"
    elif speed_kmh < 15:
        return "cycling"
    elif speed_kmh < 50:
        return "car"
    elif speed_kmh < 100:
        return "bus"
    elif speed_kmh < 300:
        return "train"
    else:
        return "plane"


def get_climatiq_emission(mode, distance_km):
    """Get CO2 emission from Climatiq API."""
    if not CLIMATIQ_API_KEY:
        # Fallback to heuristics
        factors = {"car": 0.21, "bus": 0.089, "bike": 0.0, "train": 0.041, "plane": 0.255, "walking": 0.0}
        return distance_km * factors.get(mode, 0.2)

    # Map mode to Climatiq activity_id
    activity_map = {
        "car": "passenger_vehicle-vehicle_type_car-fuel_source_na-engine_size_na-vehicle_age_na-vehicle_weight_na",
        "bus": "passenger_vehicle-vehicle_type_bus-fuel_source_na-engine_size_na-vehicle_age_na-vehicle_weight_na",
        "train": "passenger_train-route_type_commuter_rail-fuel_source_na",
        "plane": "passenger_flight-route_type_domestic-aircraft_type_na-distance_na-class_na",
        "bike": "passenger_vehicle-vehicle_type_bicycle-fuel_source_na",
        "walking": "passenger_vehicle-vehicle_type_pedestrian-fuel_source_na"
    }
    activity_id = activity_map.get(mode, activity_map["car"])

    url = "https://api.climatiq.io/estimate"
    headers = {"Authorization": f"Bearer {CLIMATIQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "emission_factor": {"activity_id": activity_id},
        "parameters": {"distance": distance_km, "distance_unit": "km"}
    }

    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            result = response.json()
            return result["co2e"] / 1000  # Convert to kg
        else:
            print(f"Climatiq API error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Climatiq API request failed: {e}")

    # Fallback
    factors = {"car": 0.21, "bus": 0.089, "bike": 0.0, "train": 0.041, "plane": 0.255, "walking": 0.0}
    return distance_km * factors.get(mode, 0.2)


class DetectTravelRequest(BaseModel):
    locations: List[List[float]] = Field(..., description="List of [lat, lon] coordinates")
    timestamps: List[str] = Field(..., description="ISO timestamps matching locations")
    consent: bool = Field(False, description="Explicit permission to analyze location data")


class CalculateFootprintRequest(BaseModel):
    mode: str
    distance_km: float
    fuel_efficiency: Optional[float] = None
    occupancy: Optional[int] = None
    flight_class: Optional[str] = None


class TranslateImpactRequest(BaseModel):
    co2_kg: float
    user_location: Optional[str] = None
    preference_context: Optional[Dict] = None


class SuggestAlternativesRequest(BaseModel):
    original_trip_data: Dict
    user_constraints: Optional[Dict] = None


@mcp.tool(annotations={"requiresConsent": True})
def john_doe_detect_travel_mode(request: DetectTravelRequest) -> dict:
    """Analyze GPS points to determine primary travel mode. Requires explicit consent."""
    if not request.consent:
        return {"error": "permission_required", "message": "Consent required to analyze location data."}

    locations = request.locations
    timestamps = request.timestamps

    if len(locations) < 2 or len(timestamps) < 2:
        return {"error": "insufficient_data", "message": "Need at least 2 location points with timestamps."}

    # Calculate average speed
    avg_speed = calculate_speed_kmh(locations, timestamps)
    mode = infer_travel_mode(avg_speed)

    # Get start and end locations
    start_lat, start_lon = locations[0]
    end_lat, end_lon = locations[-1]
    start_location = reverse_geocode(start_lat, start_lon)
    end_location = reverse_geocode(end_lat, end_lon)

    # Calculate total distance
    total_distance = 0
    for i in range(1, len(locations)):
        lat1, lon1 = locations[i-1]
        lat2, lon2 = locations[i]
        total_distance += haversine_distance(lat1, lon1, lat2, lon2)

    segments = [{"start": 0, "end": len(locations)-1, "mode": mode, "distance_km": total_distance}]

    try:
        user = get_authenticated_user()
        print(f"john_doe_detect_travel_mode called by: {user.email}")
    except Exception:
        print("john_doe_detect_travel_mode called by unauthenticated user")

    return {
        "mode": mode,
        "confidence": 0.8,  # Placeholder confidence
        "segments": segments,
        "total_distance_km": total_distance,
        "average_speed_kmh": avg_speed,
        "start_location": start_location,
        "end_location": end_location
    }


@mcp.tool()
def john_doe_calculate_carbon_footprint(request: CalculateFootprintRequest) -> dict:
    """Return CO2 emissions (kg) using Climatiq API or fallback heuristics."""
    co2_kg = get_climatiq_emission(request.mode, request.distance_km)

    try:
        user = get_authenticated_user()
        print(f"john_doe_calculate_carbon_footprint called by: {user.email}")
    except Exception:
        print("john_doe_calculate_carbon_footprint called by unauthenticated user")

    return {
        "mode": request.mode,
        "distance_km": request.distance_km,
        "co2_kg": co2_kg,
        "source": "climatiq_api" if CLIMATIQ_API_KEY else "fallback_heuristic"
    }


@mcp.tool()
def john_doe_translate_to_tangible_impact(request: TranslateImpactRequest) -> list:
    """Convert `co2_kg` into 3–5 vivid analogies ranked by impact."""
    try:
        user = get_authenticated_user()
        print(f"john_doe_translate_to_tangible_impact called by: {user.email}")
    except Exception:
        print("john_doe_translate_to_tangible_impact called by unauthenticated user")

    co2_kg = request.co2_kg
    return [
        f"{co2_kg:.2f} kg CO₂ ≈ burning {co2_kg*0.5:.1f} kg of coal",
        f"{co2_kg:.2f} kg CO₂ ≈ driving {co2_kg*3.0:.1f} km in an average car",
        f"{co2_kg:.2f} kg CO₂ ≈ charging {int(co2_kg*100)} smartphones",
    ]


@mcp.tool()
def john_doe_suggest_eco_alternatives(request: SuggestAlternativesRequest) -> list:
    """Provide ranked alternatives with estimated savings and trade-offs."""
    distance = request.original_trip_data.get("distance_km", 0)

    try:
        user = get_authenticated_user()
        print(f"john_doe_suggest_eco_alternatives called by: {user.email}")
    except Exception:
        print("john_doe_suggest_eco_alternatives called by unauthenticated user")

    alternatives = [
        {"mode": "bike", "estimated_time_minutes": int(distance * 4), "cost_delta": -1.0, "estimated_saved_kg": distance * 0.21},
        {"mode": "bus", "estimated_time_minutes": int(distance * 2), "cost_delta": -0.5, "estimated_saved_kg": distance * (0.21 - 0.089)},
    ]

    return alternatives


if __name__ == "__main__":
    print("Starting EcoGuide MCP server with tools: john_doe_detect_travel_mode, john_doe_calculate_carbon_footprint, john_doe_translate_to_tangible_impact, john_doe_suggest_eco_alternatives")
    print("Integrates with Climatiq.io for emissions and OpenStreetMap for location context.")
    print("Set CLIMATIQ_API_KEY environment variable for real emissions data.")
    print("Run with transport=streamable-http to connect from North.")
    mcp.run(transport="streamable-http")

from mcp.server import Server  # Standard MCP server
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from north_mcp_python_sdk import NorthMCPServer
from north_mcp_python_sdk.auth import get_authenticated_user
from pathlib import Path
import requests
import os
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2
import random

"""MCP server example exposing EcoGuide tools using firstname_lastname_ style names
and Pydantic request models for safer input validation.

Tool naming convention: john_doe_<tool_name> (replace `john_doe` with your own firstname_lastname)

Integrates with Climatiq.io for carbon calculations and OpenStreetMap for location context.
"""

PROMPT_PATH = Path(__file__).parents[1] / "system_prompts" / "eco_guide.md"

_default_port = 3002

# API keys from environment
CLIMATIQ_API_KEY = os.getenv("CLIMATIQ_API_KEY")
if not CLIMATIQ_API_KEY:
    print("Warning: CLIMATIQ_API_KEY not set. Carbon calculations will use fallback heuristics.")

# Create FastAPI app
app = FastAPI()
standard_mcp = Server(app)  # Add this after creating app

# Your existing NorthMCPServer
mcp = NorthMCPServer("EcoGuide MCP Server", host="0.0.0.0", port=_default_port, app=app)

@app.get("/.well-known/mcp/sse")
async def mcp_sse(request: Request):
    return StreamingResponse(standard_mcp.handle_sse(request), media_type="text/event-stream")

@app.post("/.well-known/mcp/api")
async def mcp_api(request: Request):
    return await standard_mcp.handle_json_request(request)


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


class TravelCompletionRequest(BaseModel):
    locations: List[List[float]] = Field(..., description="List of [lat, lon] coordinates from the trip")
    timestamps: List[str] = Field(..., description="ISO timestamps matching locations")
    mode: Optional[str] = Field(None, description="Detected or provided travel mode")
    distance_km: Optional[float] = Field(None, description="Trip distance in km")


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
    """Convert `co2_kg` into a single vivid analogy selected based on environmental impact."""
    try:
        user = get_authenticated_user()
        print(f"john_doe_translate_to_tangible_impact called by: {user.email}")
    except Exception:
        print("john_doe_translate_to_tangible_impact called by unauthenticated user")

    co2_kg = request.co2_kg
    analogies = [
        # Environmental impact (high)
        f"🌳 That's equivalent to cutting down {co2_kg / 21:.1f} trees (each absorbs ~21kg CO2/year)",
        f"🗑️ Like throwing away {co2_kg / 0.04:.0f} plastic bottles (each produces ~0.04kg CO2)",
        
        # Energy equivalents (medium)
        f"⚡ Enough to power a laptop for {co2_kg / 0.008:.0f} hours of streaming",
        f"🏠 Equivalent to {co2_kg / 2.5:.1f} hours of an average household's electricity",
        
        # Food equivalents (low)
        f"🍔 Equal to the carbon footprint of {co2_kg / 2.5:.1f} hamburgers",
        f"🥛 Like producing {co2_kg / 1.2:.1f} liters of milk",
    ]
    
    # Select category based on impact level
    if co2_kg > 10:
        category = analogies[:2]  # Environmental
    elif co2_kg > 1:
        category = analogies[2:4]  # Energy
    else:
        category = analogies[4:]  # Food
    
    return [random.choice(category)]


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


@mcp.tool()
def john_doe_notify_travel_completion(request: TravelCompletionRequest) -> dict:
    """Generate a notification for completed travel, including carbon impact analogy."""
    locations = request.locations
    timestamps = request.timestamps
    mode = request.mode
    distance_km = request.distance_km

    # If mode or distance not provided, detect them
    if not mode or not distance_km:
        if len(locations) >= 2 and len(timestamps) >= 2:
            avg_speed = calculate_speed_kmh(locations, timestamps)
            mode = infer_travel_mode(avg_speed)
            distance_km = sum(haversine_distance(locations[i-1][0], locations[i-1][1], locations[i][0], locations[i][1]) 
                            for i in range(1, len(locations)))
        else:
            return {"error": "insufficient_data", "message": "Need locations, timestamps, or provided mode/distance."}

    # Calculate footprint
    co2_kg = get_climatiq_emission(mode, distance_km)

    # Get analogy (inline to avoid calling tool)
    if co2_kg > 10:
        category = [
            f"🌳 That's equivalent to cutting down {co2_kg / 21:.1f} trees (each absorbs ~21kg CO2/year)",
            f"🗑️ Like throwing away {co2_kg / 0.04:.0f} plastic bottles (each produces ~0.04kg CO2)",
        ]
    elif co2_kg > 1:
        category = [
            f"⚡ Enough to power a laptop for {co2_kg / 0.008:.0f} hours of streaming",
            f"🏠 Equivalent to {co2_kg / 2.5:.1f} hours of an average household's electricity",
        ]
    else:
        category = [
            f"🍔 Equal to the carbon footprint of {co2_kg / 2.5:.1f} hamburgers",
            f"🥛 Like producing {co2_kg / 1.2:.1f} liters of milk",
        ]
    analogy = random.choice(category)

    # Get start/end locations
    start_location = reverse_geocode(locations[0][0], locations[0][1]) if locations else "Unknown"
    end_location = reverse_geocode(locations[-1][0], locations[-1][1]) if locations else "Unknown"

    notification = {
        "message": f"🚗 Travel completed! From {start_location} to {end_location} via {mode} ({distance_km:.1f} km).",
        "carbon_impact": f"You produced {co2_kg:.2f} kg CO₂. {analogy}",
        "suggestion": "Next time, consider biking or public transit to reduce your footprint!"
    }

    try:
        user = get_authenticated_user()
        print(f"john_doe_notify_travel_completion called by: {user.email}")
    except Exception:
        print("john_doe_notify_travel_completion called by unauthenticated user")

    return notification


if __name__ == "__main__":
    mcp.run(transport="streamable-http")



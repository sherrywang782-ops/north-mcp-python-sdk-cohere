from north_mcp_python_sdk import NorthMCPServer
from north_mcp_python_sdk.auth import get_authenticated_user
from pathlib import Path

"""MCP server example exposing EcoGuide tools:

- `detect_travel_mode(locations, timestamps, consent)`
- `calculate_carbon_footprint(mode, distance_km)`
- `translate_to_tangible_impact(co2_kg, user_location=None)`
- `suggest_eco_alternatives(original_trip_data, user_constraints=None)`

This server is intended to be run locally and connected to North.
"""

PROMPT_PATH = Path(__file__).parents[1] / "system_prompts" / "eco_guide.md"


mcp = NorthMCPServer("EcoGuide MCP Server", port=5222)


@mcp.tool()
def detect_travel_mode(locations: list, timestamps: list, consent: bool = False) -> dict:
    """Analyze GPS points to determine primary travel mode.

    Requires explicit `consent` to run. Returns mode, confidence, and segments.
    """
    if not consent:
        return {"error": "permission_required", "message": "Consent required to analyze location data."}

    # Simple heuristic placeholder
    mode = "car"
    confidence = 0.95
    segments = [{"start": 0, "end": max(0, len(locations) - 1), "mode": mode}]

    try:
        user = get_authenticated_user()
        print(f"detect_travel_mode called by: {user.email}")
    except Exception:
        print("detect_travel_mode called by unauthenticated user")

    return {"mode": mode, "confidence": confidence, "segments": segments}


@mcp.tool()
def calculate_carbon_footprint(mode: str, distance_km: float, **kwargs) -> dict:
    """Return CO2 emissions (kg) with a simple breakdown.

    Mode-specific parameters (fuel_efficiency, occupancy, flight_class) may be passed.
    """
    factors = {"car": 0.21, "bus": 0.089, "bike": 0.0, "train": 0.041, "plane": 0.255}
    factor = factors.get(mode, 0.2)
    co2_kg = distance_km * factor

    try:
        user = get_authenticated_user()
        print(f"calculate_carbon_footprint called by: {user.email}")
    except Exception:
        print("calculate_carbon_footprint called by unauthenticated user")

    return {"mode": mode, "distance_km": distance_km, "co2_kg": co2_kg}


@mcp.tool()
def translate_to_tangible_impact(co2_kg: float, user_location: str = None, preference_context: dict = None) -> list:
    """Convert `co2_kg` into 3–5 vivid analogies ranked by impact."""
    try:
        user = get_authenticated_user()
        print(f"translate_to_tangible_impact called by: {user.email}")
    except Exception:
        print("translate_to_tangible_impact called by unauthenticated user")

    return [
        f"{co2_kg:.2f} kg CO₂ ≈ burning {co2_kg*0.5:.1f} kg of coal",
        f"{co2_kg:.2f} kg CO₂ ≈ driving {co2_kg*3.0:.1f} km in an average car",
        f"{co2_kg:.2f} kg CO₂ ≈ charging {int(co2_kg*100)} smartphones",
    ]


@mcp.tool()
def suggest_eco_alternatives(original_trip_data: dict, user_constraints: dict = None) -> list:
    """Provide ranked alternatives with estimated savings and trade-offs."""
    distance = original_trip_data.get("distance_km", 0)
    mode = original_trip_data.get("mode", "car")

    try:
        user = get_authenticated_user()
        print(f"suggest_eco_alternatives called by: {user.email}")
    except Exception:
        print("suggest_eco_alternatives called by unauthenticated user")

    alternatives = [
        {"mode": "bike", "estimated_time_minutes": int(distance * 4), "cost_delta": -1.0, "estimated_saved_kg": distance * 0.21},
        {"mode": "bus", "estimated_time_minutes": int(distance * 2), "cost_delta": -0.5, "estimated_saved_kg": distance * (0.21 - 0.089)},
    ]

    return alternatives


if __name__ == "__main__":
    print("Starting EcoGuide MCP server with tools: detect_travel_mode, calculate_carbon_footprint, translate_to_tangible_impact, suggest_eco_alternatives")
    print("Run with transport=streamable-http to connect from North.")
    mcp.run(transport="streamable-http")

import os
import requests
from flask import Flask, request, jsonify
from collections import Counter
from datetime import datetime, timedelta
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
from cachetools import TTLCache
from flask_talisman import Talisman
from werkzeug.exceptions import HTTPException
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
Talisman(app)  # Secure headers

# Initialize rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["15000 per day", "600 per hour"]
)

# Use environment variables for sensitive information
BASE_URL = os.getenv("CODEFORCES_API_URL", "https://codeforces.com/api")
CACHE_TTL = int(os.getenv("CACHE_TTL", 1800))  # Cache time-to-live in seconds
cache = TTLCache(maxsize=100, ttl=CACHE_TTL)

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

def fetch_data(url, params):
    cache_key = (url, frozenset(params.items()))
    if cache_key in cache:
        return cache[cache_key]
    response = requests.get(url, params=params).json()
    cache[cache_key] = response
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    # Log the error
    logger.error(f"An error occurred: {str(e)}")
    # Handle HTTP exceptions
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    # Handle non-HTTP exceptions
    return jsonify({"error": "An unexpected error occurred"}), 500

@app.route("/" , methods = ["GET"])
def hello():
    return jsonify({"hello": "Username is required"}), 200
@app.route("/generate_wrapped", methods=["POST"])
@limiter.limit("50 per minute")  # More conservative rate limiting
def generate_wrapped():
    data = request.json
    username = data.get("username")
    if not username:
        return jsonify({"error": "Username is required"}), 400

    # Validate username input
    if not isinstance(username, str) or not username.isalnum():
        return jsonify({"error": "Invalid username format"}), 400

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            user_info_future = executor.submit(fetch_data, f"{BASE_URL}/user.info", {"handles": username})
            user_status_future = executor.submit(fetch_data, f"{BASE_URL}/user.status", {"handle": username})
            user_rating_future = executor.submit(fetch_data, f"{BASE_URL}/user.rating", {"handle": username})
            tourist_info_future = executor.submit(fetch_data, f"{BASE_URL}/user.info", {"handles": "tourist"})

            user_info = user_info_future.result()
            user_status = user_status_future.result()
            user_rating = user_rating_future.result()
            tourist_info = tourist_info_future.result()

        # Handle API errors
        if any(info["status"] != "OK" for info in [user_info, user_status, user_rating, tourist_info]):
            return jsonify({"error": "Failed to fetch data from Codeforces API"}), 500

        # Extract relevant data
        user_data = user_info["result"][0]
        submissions = user_status["result"]
        contests = user_rating["result"]
        HIGHEST_RATED_USER = tourist_info["result"][0]["rating"]

        # Current rank and max rank
        current_rank = user_data.get("rank", "N/A")
        max_rank = user_data.get("maxRank", "N/A")

        # Total and yearly problems solved
        total_solved = {f"{sub['problem']['contestId']}-{sub['problem']['index']}" for sub in submissions if sub["verdict"] == "OK"}
        year_start = datetime(datetime.now().year, 1, 1).timestamp()
        yearly_solved = {f"{sub['problem']['contestId']}-{sub['problem']['index']}" for sub in submissions if sub["verdict"] == "OK" and sub["creationTimeSeconds"] >= year_start}

        # Longest streak calculation
        solved_days = sorted({datetime.utcfromtimestamp(sub["creationTimeSeconds"]).date() for sub in submissions if sub["verdict"] == "OK"})
        longest_streak, current_streak = 0, 1
        for i in range(1, len(solved_days)):
            if solved_days[i] == solved_days[i - 1] + timedelta(days=1):
                current_streak += 1
            else:
                longest_streak = max(longest_streak, current_streak)
                current_streak = 1
        longest_streak = max(longest_streak, current_streak)

        # Favorite topic
        topics = [tag for sub in submissions if sub["verdict"] == "OK" for tag in sub["problem"].get("tags", [])]
        favorite_topic = Counter(topics).most_common(1)[0][0] if topics else "N/A"

        # Highest rank during a contest
        highest_rank = min([contest["rank"] for contest in contests]) if contests else "N/A"

        # Contests participated
        contests_participated = len(contests)

        # Rating improvement
        yearly_contests = [c for c in contests if c["ratingUpdateTimeSeconds"] >= year_start]
        start_rating = yearly_contests[0]["oldRating"] if yearly_contests else user_data.get("rating", 0)
        end_rating = yearly_contests[-1]["newRating"] if yearly_contests else user_data.get("rating", 0)
        rating_improvement = end_rating - start_rating

        # Global percentile (based on highest-rated coder)
        global_percentile = f"Top { (user_data['rating'] / HIGHEST_RATED_USER) * 100:.2f}%" if "rating" in user_data else "N/A"

        # Best-performing month
        submission_months = [datetime.utcfromtimestamp(sub["creationTimeSeconds"]).strftime("%B") for sub in submissions if sub["verdict"] == "OK"]
        best_month = Counter(submission_months).most_common(1)[0][0] if submission_months else "N/A"

        # Prepare milestones with a mapping of conditions to messages
        milestone_conditions = {
            len(total_solved) >= 500: "Solved 500+ problems",
            current_rank in ["grandmaster", "legendary grandmaster"]: "Achieved Grandmaster rank",
            user_data.get("rating", 0) >= 3000: "Reached a rating of 3000+",
            contests_participated >= 10: "Participated in 10+ contests",
            longest_streak >= 7: "Maintained a streak of 7+ days",
            len(yearly_solved) >= 100: "Solved 100+ problems this year"
        }

        # Collect milestones based on conditions
        milestones = [message for condition, message in milestone_conditions.items() if condition]

        # Prepare response
        response = {
            "username": username,
            "current_rank": current_rank,
            "highest_rank": highest_rank,
            "longest_streak": longest_streak,
            "problems_solved_this_year": len(yearly_solved),
            "total_problems_solved": len(total_solved),
            "favorite_topic": favorite_topic,
            "contests_participated": contests_participated,
            "rating_improvement": rating_improvement,
            "best_month": best_month,
            "motivational_message": "Keep pushing your limits!",
            "global_percentile": global_percentile
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Exception in generate_wrapped: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=False)

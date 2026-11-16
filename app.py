from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlencode
import base64
from flask import (
    Flask, Response, redirect, request,
    send_file, send_from_directory, url_for, render_template
)


PROJECT_DIR = Path(__file__).resolve().parent
LONGITUDE_MIN = 202.008
LONGITUDE_MAX = 202.163
LATITUDE_MIN = 39.677
LATITUDE_MAX = 39.937
DEFAULT_LONGITUDE = "202.0800"
DEFAULT_LATITUDE = "39.8100"
MAX_QUERY_LENGTH = 400


def locate_script():
    """Find the supplied AresTrail script without changing its contents."""
    direct_script = PROJECT_DIR / "AresTrailFinal.py"
    if direct_script.is_file():
        return direct_script

    search_dirs = [PROJECT_DIR, PROJECT_DIR / "attached_assets"]
    candidates = []
    for directory in search_dirs:
        if directory.is_dir():
            candidates.extend(directory.glob("AresTrailFinal*.py"))
    return sorted(candidates)[0] if candidates else direct_script


SCRIPT_PATH = locate_script()
OUTPUT_DIR = SCRIPT_PATH.parent / "outputs"
SCENE_PATH = PROJECT_DIR / "martian-sunset-blue.jpg"
if not SCENE_PATH.is_file():
    SCENE_PATH = PROJECT_DIR / "attached_assets" / "martian-sunset-blue.jpg"
if not SCENE_PATH.is_file():
    SCENE_PATH = PROJECT_DIR / "mars_scene_wide.jpg"
if not SCENE_PATH.is_file():
    SCENE_PATH = PROJECT_DIR / "mars_scene.jpg"

# index.html is plain HTML so it also works when opened without Jinja rendering.
app = Flask(__name__)
run_lock = threading.Lock()


def page_error(message, diagnostic=None, longitude=DEFAULT_LONGITUDE,
               latitude=DEFAULT_LATITUDE, query=""):
    if diagnostic:
        app.logger.warning("AresTrail calculation detail: %s", str(diagnostic)[-3500:])

    params = {
        "error": message,
        "longitude": longitude,
        "latitude": latitude,
    }
    if query:
        params["query"] = query
    return redirect(f"{url_for('home')}?{urlencode(params)}")


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/martian-sunset-blue.jpg")
def mars_scene():
    if not SCENE_PATH.is_file():
        return "The Mars scene image is missing.", 404
    return send_file(SCENE_PATH, mimetype="image/jpeg", conditional=True)


@app.post("/run")
def run_ares_trail():
    longitude = request.form.get("longitude", "").strip()
    latitude = request.form.get("latitude", "").strip()
    query = request.form.get("query", "").strip()

    try:
        longitude_value = float(longitude)
        latitude_value = float(latitude)
    except (TypeError, ValueError):
        return page_error(
            "Enter valid numeric longitude and latitude values.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    if not LONGITUDE_MIN <= longitude_value <= LONGITUDE_MAX:
        return page_error(
            f"Longitude must be between {LONGITUDE_MIN} and {LONGITUDE_MAX}°E "
            "for the AP-1 map.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    if not LATITUDE_MIN <= latitude_value <= LATITUDE_MAX:
        return page_error(
            f"Latitude must be between {LATITUDE_MIN} and {LATITUDE_MAX}° "
            "for the AP-1 map.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    if not query:
        return page_error(
            "Enter what you want to explore on Mars.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    if len(query) > MAX_QUERY_LENGTH:
        return page_error(
            f"Keep the exploration request under {MAX_QUERY_LENGTH} characters.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    if not SCRIPT_PATH.is_file():
        return page_error(
            "AresTrailFinal.py was not found. Place it beside app.py and index.html.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    if not run_lock.acquire(blocking=False):
        return page_error(
            "A AresTrail calculation is already running. Wait for it to finish before starting another.",
            longitude=longitude,
            latitude=latitude,
            query=query,
        )

    try:
        # AresTrailFinal.py reads longitude, latitude, then the Gemini request
        # from stdin. The browser form supplies those same inputs in that order.
        scripted_input = f"{longitude_value}\n{latitude_value}\n{query}\n"

        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH)],
                input=scripted_input,
                text=True,
                capture_output=True,
                cwd=SCRIPT_PATH.parent,
                timeout=105,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            diagnostic = exc.stderr or exc.stdout or (
                "The calculation exceeded the 30-minute time limit."
            )
            if isinstance(diagnostic, bytes):
                diagnostic = diagnostic.decode("utf-8", errors="replace")
            return page_error(
                "The route calculation timed out. Check the raster files and try again.",
                str(diagnostic)[-3500:],
                longitude,
                latitude,
                query,
            )

        if result.returncode != 0:
            diagnostic = result.stderr or result.stdout or (
                "The Python script exited without an error message."
            )
            return page_error(
                "AresTrail could not complete this calculation. The diagnostic output is shown below.",
                diagnostic[-3500:],
                longitude,
                latitude,
                query,
            )

        results_page = OUTPUT_DIR / "index.html"
        if not results_page.is_file():
            return page_error(
                "The calculation finished, but outputs/index.html was not created.",
                (result.stdout or "")[-3500:],
                longitude,
                latitude,
                query,
            )
    finally:
        run_lock.release()

    html = results_page.read_text(encoding="utf-8")

    for filename in ("AP1_Route_CTX.png", "AP1_Route_Map.png"):
        image_path = OUTPUT_DIR / filename

        if not image_path.is_file():
            return page_error(
                f"The calculation finished, but {filename} was not created.",
                longitude=longitude,
                latitude=latitude,
                query=query
            )

        encoded_image = base64.b64encode(
            image_path.read_bytes()
        ).decode("ascii")

        html = html.replace(
            f'src="/results/{filename}"',
            f'src="data:image/png;base64,{encoded_image}"'
        )

    return Response(html, mimetype="text/html")


@app.get("/results")
def results():
    results_page = OUTPUT_DIR / "index.html"
    if not results_page.is_file():
        return redirect(url_for("home"))
    return send_from_directory(OUTPUT_DIR, "index.html")


@app.get("/results/<path:filename>")
def result_assets(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    print("AresTrail browser interface: http://127.0.0.1:8000")
    print("Keep this terminal window open while using the browser app.")
    app.run(host="127.0.0.1", port=8000, debug=False)
